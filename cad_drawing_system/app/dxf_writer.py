"""Generate a DXF drawing set from a BuildingModel.

Everything is drawn once in model space on AIA-style layers, each level
side by side. Each sheet is a paper-space layout (A3 landscape) with a scaled
viewport that freezes the layers that sheet should not show, plus title
block, legend and scale bar. Sheet numbers, titles and the sheets issued at
each stage follow the ADC Interior Design Drawing Register.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Layout, Paperspace
from shapely.affinity import rotate as s_rotate
from shapely.geometry import LineString, Polygon
from shapely.geometry import Point as SPoint
from shapely.ops import polylabel, unary_union

from . import geometry as g
from .schema import STAGE_NAMES, BuildingModel, Finish, Furniture, Opening, Wall

# ---------------------------------------------------------------- sheet constants (mm on paper)

PAPER_W, PAPER_H = 420.0, 297.0  # A3 landscape
PAPER_NAME = "A3"
FRAME = (20.0, 10.0, 410.0, 287.0)  # x0, y0, x1, y1 (wider left margin for binding)
STRIP_X = 335.0  # title strip starts here
PLAN_AREA = (25.0, 34.0, 330.0, 282.0)  # where the viewport goes
SCALES = [20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500]
DIM_MARGIN = 30.0  # paper mm needed outside the plan for dimension chains
LEVEL_GAP = 20000.0  # mm between levels laid side by side in model space

LOGO_FILE = Path(__file__).parent / "assets" / "aalia_logo.png"
LOGO_BOX = (STRIP_X + 4, 244.0, FRAME[2] - 4, 284.0)  # x0, y0, x1, y1 on paper
LOGO_PX = (1400, 861)
LEGEND_TOP = 214.0

ALIGN = TextEntityAlignment

# name: (colour, lineweight in 1/100 mm, linetype)
LAYERS = {
    "A-WALL": (7, 50, "Continuous"),
    "A-WALL-PATT": (8, 0, "Continuous"),
    "A-DOOR": (7, 25, "Continuous"),
    "A-DOOR-HEAD": (7, 18, "Continuous"),
    "A-GLAZ": (7, 18, "Continuous"),
    "A-DOOR-IDEN": (7, 18, "Continuous"),
    "A-FLOR-LEVL": (7, 25, "Continuous"),
    "A-FLOR-OPEN": (7, 25, "Continuous"),
    "A-ANNO-DIMS": (7, 13, "Continuous"),
    "A-ANNO-DIMS-INTR": (7, 13, "Continuous"),
    "G-ROOM-NAME": (7, 25, "Continuous"),
    "A-AREA-IDEN": (7, 18, "Continuous"),
    "I-FURN": (7, 18, "Continuous"),
    "I-FURN-IDEN": (7, 13, "Continuous"),
    "I-CLNG": (7, 18, "Continuous"),
    "I-CLNG-IDEN": (7, 18, "Continuous"),
    "I-FLOR-PATT": (9, 9, "Continuous"),
    "I-FLOR-IDEN": (7, 18, "Continuous"),
    "M-HVAC-EQPM": (7, 35, "Continuous"),
    "M-HVAC-IDEN": (7, 18, "Continuous"),
    "M-HVAC-DIMS": (7, 13, "Continuous"),
    "G-ANNO-TTLB": (7, 25, "Continuous"),
    "G-ANNO-TEXT": (7, 18, "Continuous"),
    "G-ANNO-NPLT": (8, 0, "Continuous"),
}

WALL_FILL = {"exterior": 8, "interior": 8, "partition": 9}
STATUS_SUFFIX = {"reuse": "R", "modify": "M", "relocate": "RL", "remove": "X"}
STATUS_LABEL = {"": "TBC", "new": "New", "reuse": "Reuse", "modify": "Modify", "relocate": "Relocate", "remove": "Remove"}


@dataclass
class SheetSpec:
    number: str  # register number without level suffix, e.g. "ID-201"
    title: str
    show: set[str] = field(default_factory=set)
    legend: Callable[["Ctx", Paperspace, float], None] | None = None
    stages: frozenset[str] = frozenset(STAGE_NAMES)
    needs: str = ""  # model collection that must be non-empty
    north: bool = True
    note: str = ""


@dataclass
class Ctx:
    model: BuildingModel
    doc: ezdxf.document.Drawing
    msp: Layout
    scale: int
    geo: g.WallGeometry
    room_net: dict[str, Polygon] = field(default_factory=dict)
    level_id: str = "GF"
    suffix: str = ""  # ".GF" on multi-level projects
    logo_def: object = None  # shared IMAGEDEF of the company logo

    def h(self, paper_mm: float) -> float:
        """Model-space size that prints at `paper_mm` on the sheet."""
        return paper_mm * self.scale


# ---------------------------------------------------------------- numbering

def sheet_number(model: BuildingModel, short: str) -> str:
    """ADC-[Project code]-ID-[Series][Sheet]-[Revision], e.g. ADC-VLA24-ID-203-B."""
    p = model.project
    return f"ADC-{p.project_code or 'XXXX'}-{short}-{p.revision}"


def level_text(ffl: float) -> str:
    """Levels are written in metres: FFL ±0.00, FFL +0.40."""
    return "FFL ±0.00" if abs(ffl) < 0.5 else f"FFL {ffl / 1000:+.2f}"


# ---------------------------------------------------------------- document setup

def choose_scale(model: BuildingModel) -> int:
    x0, y0, x1, y1 = g.extents(model)
    aw = PLAN_AREA[2] - PLAN_AREA[0] - 2 * DIM_MARGIN
    ah = PLAN_AREA[3] - PLAN_AREA[1] - 2 * DIM_MARGIN
    for s in SCALES:
        if (x1 - x0) / s <= aw and (y1 - y0) / s <= ah:
            return s
    return SCALES[-1]


def _setup(doc, scale: int) -> None:
    doc.header["$INSUNITS"] = 4  # mm
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LUNITS"] = 2
    doc.header["$PSLTSCALE"] = 1
    doc.header["$LTSCALE"] = 1
    for name, (color, lw, lt) in LAYERS.items():
        layer = doc.layers.add(name, color=color, linetype=lt)
        layer.dxf.lineweight = lw
    doc.layers.get("G-ANNO-NPLT").dxf.plot = 0
    doc.styles.add("ADC", font="arial.ttf")
    ds = doc.dimstyles.new("ADC-ARCH")
    ds.dxf.dimtxsty = "ADC"
    ds.dxf.dimtxt = 2.2
    ds.dxf.dimtsz = 1.0  # architectural ticks instead of arrows
    ds.dxf.dimasz = 1.0
    ds.dxf.dimexo = 1.5
    ds.dxf.dimexe = 1.2
    ds.dxf.dimdle = 1.0
    ds.dxf.dimgap = 0.8
    ds.dxf.dimtad = 1
    ds.dxf.dimtih = 0
    ds.dxf.dimtoh = 0
    ds.dxf.dimdec = 0
    ds.dxf.dimzin = 8
    ds.dxf.dimscale = scale
    ds.dxf.dimclrd = 7
    ds.dxf.dimclre = 7
    ds.dxf.dimlwd = 13
    ds.dxf.dimlwe = 13


def _text(space, text: str, pos, height: float, layer: str, align=ALIGN.MIDDLE_CENTER, rotation=0.0):
    t = space.add_text(text, height=height, rotation=rotation, dxfattribs={"layer": layer, "style": "ADC"})
    t.set_placement(pos, align=align)
    return t


def _poly(space, pts, layer: str, closed=True, **attrs):
    return space.add_lwpolyline(pts, close=closed, dxfattribs={"layer": layer, **attrs})


def _rect(space, x0, y0, x1, y1, layer, **attrs):
    return _poly(space, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer, **attrs)


def _hatch_polygon(space, poly: Polygon, layer: str, color: int | None = None):
    h = space.add_hatch(color=color if color is not None else 256, dxfattribs={"layer": layer})
    h.paths.add_polyline_path(list(poly.exterior.coords)[:-1], is_closed=True, flags=1)
    for hole in poly.interiors:
        h.paths.add_polyline_path(list(hole.coords)[:-1], is_closed=True, flags=0)
    return h


# ---------------------------------------------------------------- walls & openings

def draw_walls(ctx: Ctx) -> None:
    for kind, geom in ctx.geo.by_kind.items():
        for poly in g.polygons(geom):
            _hatch_polygon(ctx.msp, poly, "A-WALL-PATT", WALL_FILL[kind])
    for poly in g.polygons(ctx.geo.outline):
        _poly(ctx.msp, list(poly.exterior.coords)[:-1], "A-WALL")
        for hole in poly.interiors:
            _poly(ctx.msp, list(hole.coords)[:-1], "A-WALL")


def _arc(space, center, radius, v_from, v_to, layer):
    a1 = math.degrees(math.atan2(v_from[1], v_from[0]))
    a2 = math.degrees(math.atan2(v_to[1], v_to[0]))
    cross = v_from[0] * v_to[1] - v_from[1] * v_to[0]
    start, end = (a1, a2) if cross > 0 else (a2, a1)
    space.add_arc(center, radius, start, end, dxfattribs={"layer": layer})


def _door_leaf(ctx: Ctx, w: Wall, hinge_d: float, toward: float, leaf: float, side: float):
    """Leaf hinged at `hinge_d` along the wall, closing toward `toward` (+1/-1 along wall)."""
    t = w.thickness / 2
    face = side * t
    hinge = g.along(w, hinge_d, face)
    open_end = g.along(w, hinge_d, face + side * leaf)
    closed_end = g.along(w, hinge_d + toward * leaf, face)
    ctx.msp.add_line(hinge, open_end, dxfattribs={"layer": "A-DOOR", "lineweight": 35})
    _arc(ctx.msp, hinge, leaf,
         (closed_end[0] - hinge[0], closed_end[1] - hinge[1]),
         (open_end[0] - hinge[0], open_end[1] - hinge[1]), "A-DOOR")


def _plan_center(ctx: Ctx) -> tuple[float, float]:
    x0, y0, x1, y1 = g.extents(ctx.model)
    return (x0 + x1) / 2, (y0 + y1) / 2


def _outside_sign(ctx: Ctx, w: Wall, o: Opening) -> float:
    """+1 if the wall's left side faces away from the plan centre."""
    cx, cy = _plan_center(ctx)
    mx, my = g.along(w, o.offset + o.width / 2)
    nx, ny = g.left_normal(w)
    return 1.0 if (mx - cx) * nx + (my - cy) * ny > 0 else -1.0


def draw_openings(ctx: Ctx) -> None:
    walls = {w.id: w for w in ctx.model.walls}
    for o in ctx.model.openings:
        w = walls[o.wall_id]
        t = w.thickness / 2
        a, b = o.offset, o.offset + o.width
        side = 1.0 if o.swing == "left" else -1.0
        # head / threshold lines across the gap (RCP and flooring sheets)
        for f in (-t, t):
            ctx.msp.add_line(g.along(w, a, f), g.along(w, b, f), dxfattribs={"layer": "A-DOOR-HEAD"})

        if o.kind == "door":
            hinge_d, toward = (a, 1) if o.hinge == "start" else (b, -1)
            _door_leaf(ctx, w, hinge_d, toward, o.width, side)
        elif o.kind == "double_door":
            _door_leaf(ctx, w, a, 1, o.width / 2, side)
            _door_leaf(ctx, w, b, -1, o.width / 2, side)
        elif o.kind == "sliding_door":
            pt = 30.0
            for (s0, s1), off in (((a, a + o.width * 0.55), t / 3), ((b - o.width * 0.55, b), -t / 3)):
                pts = [g.along(w, s0, off - pt / 2), g.along(w, s1, off - pt / 2),
                       g.along(w, s1, off + pt / 2), g.along(w, s0, off + pt / 2)]
                _poly(ctx.msp, pts, "A-DOOR")
        elif o.kind == "window":
            for f in (-t, t):
                ctx.msp.add_line(g.along(w, a, f), g.along(w, b, f), dxfattribs={"layer": "A-GLAZ"})
            for f in (-15.0, 15.0):
                ctx.msp.add_line(g.along(w, a, f), g.along(w, b, f), dxfattribs={"layer": "A-GLAZ"})
        else:  # plain opening: dashed head line
            for f in (-t, t):
                ctx.msp.add_line(g.along(w, a, f), g.along(w, b, f),
                                 dxfattribs={"layer": "A-DOOR", "linetype": "DASHED", "ltscale": ctx.scale / 50})

        # tags go inside the building (the outside is reserved for dimension chains),
        # and on interior walls on the side away from the door swing
        if w.kind == "exterior":
            tag_side = -_outside_sign(ctx, w, o)
        else:
            tag_side = -side if o.kind in ("door", "double_door") else 1.0
        pos = g.along(w, (a + b) / 2, tag_side * (t + ctx.h(5)))
        r = ctx.h(2.6)
        if o.kind == "window":
            _poly(ctx.msp, [(pos[0] - r, pos[1]), (pos[0], pos[1] - r), (pos[0] + r, pos[1]), (pos[0], pos[1] + r)],
                  "A-DOOR-IDEN")
        else:
            ctx.msp.add_circle(pos, r, dxfattribs={"layer": "A-DOOR-IDEN"})
        _text(ctx.msp, o.tag, pos, ctx.h(1.6), "A-DOOR-IDEN")


# ---------------------------------------------------------------- rooms & dimensions

def _label_point(poly: Polygon) -> tuple[float, float]:
    p = polylabel(poly, tolerance=10)
    return p.x, p.y


def _poly_outline(space, poly: Polygon, layer: str, **attrs) -> None:
    _poly(space, list(poly.exterior.coords)[:-1], layer, **attrs)


def draw_levels_and_voids(ctx: Ctx) -> None:
    """Raised/lowered platforms get a step edge and level tag; voids a cross and balustrade line."""
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty or r.kind == "room":
            continue
        if r.kind == "platform":
            _poly_outline(ctx.msp, net, "A-FLOR-LEVL")
        else:  # void: balustrade line just inside the edge and a cross
            inner = net.buffer(-50, join_style="mitre")
            _poly_outline(ctx.msp, net, "A-FLOR-OPEN")
            if not inner.is_empty and isinstance(inner, Polygon):
                _poly_outline(ctx.msp, inner, "A-FLOR-OPEN")
            bx0, by0, bx1, by1 = net.bounds
            for a, b in (((bx0, by0), (bx1, by1)), ((bx0, by1), (bx1, by0))):
                seg = LineString([a, b]).intersection(net)
                for part in getattr(seg, "geoms", [seg]):
                    if isinstance(part, LineString) and part.length > 1:
                        ctx.msp.add_line(part.coords[0], part.coords[-1], dxfattribs={"layer": "A-FLOR-OPEN"})


def draw_room_tags(ctx: Ctx) -> None:
    finishes = {f.code: f for f in ctx.model.finishes}
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty:
            continue
        x, y = _label_point(net)
        if r.kind == "void":
            _text(ctx.msp, "VOID", (x, y + ctx.h(4)), ctx.h(2.8), "A-FLOR-OPEN")
            continue
        if r.kind == "platform":
            label = f"{r.name.upper()}  {level_text(ctx.model.level(ctx.level_id).ffl + r.floor_level)}"
            _text(ctx.msp, label, (x, y + ctx.h(4)), ctx.h(2.0), "A-FLOR-LEVL")
            if r.floor_finish:
                _text(ctx.msp, r.floor_finish, (x, y + ctx.h(1)), ctx.h(1.8), "I-FLOR-IDEN")
            continue
        if r.number:  # room number in a box above the name
            bw, bh = ctx.h(2.2 + 1.3 * len(r.number)), ctx.h(4)
            _rect(ctx.msp, x - bw / 2, y + ctx.h(4), x + bw / 2, y + ctx.h(4) + bh, "G-ROOM-NAME")
            _text(ctx.msp, r.number, (x, y + ctx.h(6)), ctx.h(2.2), "G-ROOM-NAME")
        _text(ctx.msp, r.name.upper(), (x, y + ctx.h(1.6)), ctx.h(2.8), "G-ROOM-NAME")
        _text(ctx.msp, f"{net.area / 1e6:.1f} m²", (x, y - ctx.h(2.2)), ctx.h(2.0), "A-AREA-IDEN")
        ceiling = r.ceiling_finish or "-"
        _text(ctx.msp, f"{ceiling}  CH +{r.ceiling_height:.0f}", (x, y - ctx.h(2.2)), ctx.h(2.0), "I-CLNG-IDEN")
        floor = r.floor_finish or "-"
        desc = finishes[floor].description if floor in finishes else ""
        _text(ctx.msp, floor, (x, y - ctx.h(2.2)), ctx.h(2.0), "I-FLOR-IDEN")
        if desc:
            _text(ctx.msp, desc[:28], (x, y - ctx.h(5.2)), ctx.h(1.6), "I-FLOR-IDEN")


def _chain(ctx: Ctx, coords: list[float], fixed: float, horizontal: bool, layer="A-ANNO-DIMS") -> None:
    pts = sorted(set(round(c) for c in coords))
    pts = [p for i, p in enumerate(pts) if i == 0 or p - pts[i - 1] > 1]
    for a, b in zip(pts, pts[1:]):
        if horizontal:
            dim = ctx.msp.add_linear_dim(base=(a, fixed), p1=(a, fixed), p2=(b, fixed), angle=0,
                                         dimstyle="ADC-ARCH", dxfattribs={"layer": layer})
        else:
            dim = ctx.msp.add_linear_dim(base=(fixed, a), p1=(fixed, a), p2=(fixed, b), angle=90,
                                         dimstyle="ADC-ARCH", dxfattribs={"layer": layer})
        dim.render()


def draw_dimensions(ctx: Ctx) -> None:
    solid = ctx.geo.solid
    if solid.is_empty:
        return
    x0, y0, x1, y1 = solid.bounds
    ring = [c for poly in g.polygons(solid) for c in poly.exterior.coords]
    walls = {w.id: w for w in ctx.model.walls}
    gap, step = ctx.h(10), ctx.h(8)

    # (horizontal?, side sign, line of the facade, outward coordinate)
    sides = [(True, 1, y1), (True, -1, y0), (False, 1, x1), (False, -1, x0)]
    for horizontal, sign, edge in sides:
        mid = (y0 + y1) / 2 if horizontal else (x0 + x1) / 2
        # 1) openings on exterior walls facing this side
        op_pts = []
        for o in ctx.model.openings:
            w = walls[o.wall_id]
            if w.kind != "exterior":
                continue
            is_h = abs(w.start.y - w.end.y) < 1
            is_v = abs(w.start.x - w.end.x) < 1
            if horizontal and is_h and (w.start.y - mid) * sign > 0:
                op_pts += [g.along(w, o.offset)[0], g.along(w, o.offset + o.width)[0]]
            elif not horizontal and is_v and (w.start.x - mid) * sign > 0:
                op_pts += [g.along(w, o.offset)[1], g.along(w, o.offset + o.width)[1]]
        rows = []
        if op_pts:
            rows.append(op_pts + ([x0, x1] if horizontal else [y0, y1]))
        rows.append([c[0] if horizontal else c[1] for c in ring])  # building outline breaks
        rows.append([x0, x1] if horizontal else [y0, y1])  # overall
        for i, row in enumerate(rows):
            line_at = edge + sign * (gap + i * step)
            _chain(ctx, row, line_at, horizontal)

    # clear internal dimensions for (near-)rectangular rooms
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty or r.kind != "room":
            continue
        bx0, by0, bx1, by1 = net.bounds
        if net.area < 0.97 * (bx1 - bx0) * (by1 - by0) or min(bx1 - bx0, by1 - by0) < ctx.h(32):
            continue  # not rectangular, or too small to dimension legibly
        # along the bottom and left faces, clear of the room name in the middle
        _chain(ctx, [bx0, bx1], by0 + ctx.h(11), True, "A-ANNO-DIMS-INTR")
        _chain(ctx, [by0, by1], bx0 + ctx.h(11), False, "A-ANNO-DIMS-INTR")


# ---------------------------------------------------------------- furniture

def _local(f: Furniture):
    c, s = math.cos(math.radians(f.rotation)), math.sin(math.radians(f.rotation))
    return lambda x, y: (f.center.x + x * c - y * s, f.center.y + x * s + y * c)


def draw_furniture_item(space, f: Furniture, layer="I-FURN") -> None:
    T = _local(f)
    W, D = f.width / 2, f.depth / 2
    rect = lambda x0, y0, x1, y1, **a: _poly(space, [T(x0, y0), T(x1, y0), T(x1, y1), T(x0, y1)], layer, **a)  # noqa: E731
    line = lambda x0, y0, x1, y1: space.add_line(T(x0, y0), T(x1, y1), dxfattribs={"layer": layer})  # noqa: E731
    circle = lambda x, y, r: space.add_circle(T(x, y), r, dxfattribs={"layer": layer})  # noqa: E731

    def ellipse(x, y, rx, ry):
        cx, cy = T(x, y)
        ax, ay = T(x + rx, y)
        space.add_ellipse((cx, cy), major_axis=(ax - cx, ay - cy), ratio=min(1.0, ry / rx) if rx else 1.0,
                          dxfattribs={"layer": layer})

    cat = f.category
    if cat == "rug":
        rect(-W, -D, W, D, linetype="DASHED", ltscale=10)
        return
    if cat == "plant":
        r = min(W, D)
        circle(0, 0, r)
        circle(0, 0, r * 0.45)
        return
    if cat in ("dining_table", "meeting_table") and abs(f.width - f.depth) < 1 and "round" in f.name.lower():
        circle(0, 0, W)
        return
    rect(-W, -D, W, D)

    if cat in ("sofa", "armchair"):
        back = D * 0.4
        arm = min(W * 0.18, 220)
        rect(-W, D - back, W, D)
        rect(-W, -D, -W + arm, D - back)
        rect(W - arm, -D, W, D - back)
        if cat == "sofa":
            n = max(1, round((2 * W - 2 * arm) / 700))
            for i in range(1, n):
                x = -W + arm + i * (2 * W - 2 * arm) / n
                line(x, -D, x, D - back)
    elif cat in ("chair", "office_chair"):
        rect(-W, D - D * 0.2, W, D)
    elif cat == "bed":
        hb = min(80.0, D * 0.05)
        line(-W, D - hb, W, D - hb)
        n = 2 if f.width >= 1200 else 1
        pw = (2 * W - 150) / n
        for i in range(n):
            px = -W + 75 + i * pw
            rect(px + 25, D - hb - 450, px + pw - 25, D - hb - 100)
        line(-W, D - hb - 650, W, D - hb - 650)
        line(-W, D - hb - 650, -W + 400, D - hb - 1050)
    elif cat == "wardrobe":
        space.add_line(T(-W + 50, 0), T(W - 50, 0), dxfattribs={"layer": layer, "linetype": "DASHED", "ltscale": 5})
        line(-W, -D, W, D)
    elif cat == "bookshelf":
        for i in range(1, 4):
            line(-W + i * 2 * W / 4, -D, -W + i * 2 * W / 4, D)
    elif cat == "tv_unit":
        rect(-W * 0.7, D - 60, W * 0.7, D - 20)
    elif cat == "fridge":
        line(-W, -D, W, D)
        line(-W, D, W, -D)
    elif cat == "cooker":
        for x in (-W / 2, W / 2):
            for y in (-D / 2, D / 2):
                circle(x, y, min(W, D) * 0.28)
    elif cat == "sink":
        rect(-W + 60, -D + 60, W - 60, D - 100)
        circle(0, 0, 25)
    elif cat == "basin":
        ellipse(0, -D * 0.1, W * 0.75, D * 0.6)
        circle(0, D * 0.6, 20)
    elif cat == "wc":
        rect(-W, D - D * 0.28, W, D)
        ellipse(0, -D * 0.2, W * 0.8, D * 0.5)
    elif cat == "shower":
        line(-W, -D, W, D)
        line(-W, D, W, -D)
        circle(0, 0, 40)
    elif cat == "bathtub":
        rect(-W + 60, -D + 60, W - 60, D - 60)
        circle(W - 200, 0, 30)
    elif cat in ("kitchen_counter",):
        line(-W, -D + 30, W, -D + 30)


def draw_furniture(ctx: Ctx) -> None:
    for f in ctx.model.furniture:
        draw_furniture_item(ctx.msp, f)
        if f.category != "rug":
            tag = f"{f.tag}-{STATUS_SUFFIX[f.status]}" if f.status in STATUS_SUFFIX else f.tag
            _text(ctx.msp, tag, (f.center.x, f.center.y), ctx.h(1.6), "I-FURN-IDEN")


# ---------------------------------------------------------------- ceiling

FIXTURE_LABELS = {
    "downlight": "Recessed downlight",
    "pendant": "Pendant light",
    "linear_light": "Linear LED light",
    "spotlight": "Adjustable spotlight",
    "wall_light": "Wall light",
    "ac_supply": "AC supply diffuser",
    "ac_return": "AC return grille",
    "smoke_detector": "Smoke detector",
    "speaker": "Ceiling speaker",
    "exhaust_fan": "Exhaust fan",
    "access_panel": "Access panel 600x600",
}


def draw_fixture(space, kind: str, x: float, y: float, k: float = 1.0, rot: float = 0.0,
                 length: float = 0.0, layer="I-CLNG", text_h: float = 0.0) -> None:
    """Fixture symbol at real size times `k` (k<1 for legends on paper)."""
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    T = lambda u, v: (x + (u * c - v * s) * k, y + (u * s + v * c) * k)  # noqa: E731
    attrs = {"layer": layer}
    sq = lambda a, **ex: _poly(space, [T(-a, -a), T(a, -a), T(a, a), T(-a, a)], layer, **ex)  # noqa: E731
    th = text_h or 70 * k
    if kind == "downlight":
        space.add_circle(T(0, 0), 100 * k, dxfattribs=attrs)
        space.add_line(T(-150, 0), T(150, 0), dxfattribs=attrs)
        space.add_line(T(0, -150), T(0, 150), dxfattribs=attrs)
    elif kind == "pendant":
        space.add_circle(T(0, 0), 150 * k, dxfattribs=attrs)
        h = space.add_hatch(color=256, dxfattribs=attrs)
        h.paths.add_edge_path().add_arc(T(0, 0), 60 * k, 0, 360)
    elif kind == "spotlight":
        space.add_circle(T(0, 0), 80 * k, dxfattribs=attrs)
        _poly(space, [T(-40, -30), T(40, -30), T(0, 50)], layer)
    elif kind == "linear_light":
        half = max(length, 600) / 2
        _poly(space, [T(-half, -40), T(half, -40), T(half, 40), T(-half, 40)], layer)
        space.add_line(T(-half, 0), T(half, 0), dxfattribs=attrs)
    elif kind == "wall_light":
        space.add_arc(T(0, 0), 120 * k, rot, rot + 180, dxfattribs=attrs)
        space.add_line(T(-120, 0), T(120, 0), dxfattribs=attrs)
    elif kind == "ac_supply":
        sq(300)
        space.add_line(T(-300, -300), T(300, 300), dxfattribs=attrs)
        space.add_line(T(-300, 300), T(300, -300), dxfattribs=attrs)
    elif kind == "ac_return":
        sq(300)
        for v in (-150, 0, 150):
            space.add_line(T(-300, v), T(300, v), dxfattribs=attrs)
    elif kind in ("smoke_detector", "speaker"):
        space.add_circle(T(0, 0), 100 * k, dxfattribs=attrs)
        _text(space, "S" if kind == "smoke_detector" else "SP", T(0, 0), th, layer)
    elif kind == "exhaust_fan":
        sq(150)
        space.add_circle(T(0, 0), 120 * k, dxfattribs=attrs)
    elif kind == "access_panel":
        sq(300, linetype="DASHED", ltscale=max(k * 10, 0.2))
        _text(space, "AP", T(0, 0), th, layer)


def draw_ceiling(ctx: Ctx) -> None:
    for c in ctx.model.ceiling_fixtures:
        draw_fixture(ctx.msp, c.kind, c.position.x, c.position.y, rot=c.rotation, length=c.length,
                     text_h=ctx.h(1.4))


# ---------------------------------------------------------------- flooring

PatternSpec = tuple[str, float, float]  # (style, spacing mm, angle)


def floor_pattern(f: Finish | None) -> PatternSpec:
    text = f"{f.description} {f.material}".lower() if f else ""
    size = re.search(r"(\d{3,4})\s*[x×]\s*(\d{3,4})", text)
    if any(k in text for k in ("tile", "porcelain", "ceramic", "marble", "granite", "stone", "terrazzo")):
        return ("grid", float(size.group(1)) if size else 600.0, 0.0)
    if any(k in text for k in ("wood", "timber", "parquet", "oak", "walnut", "laminate", "engineered", "plank", "lvt", "spc")):
        return ("lines", 190.0, 0.0)
    if "carpet" in text:
        return ("lines", 120.0, 45.0)
    if any(k in text for k in ("concrete", "screed", "epoxy", "resin", "microcement", "cement")):
        return ("lines", 450.0, 45.0)
    return ("lines", 300.0, 135.0)


def _pattern_lines(poly: Polygon, spacing: float, angle: float, origin) -> list[LineString]:
    if poly.is_empty or spacing <= 0:
        return []
    ox, oy = origin
    # rotate the polygon so the pattern lines are horizontal, generate, rotate back
    rp = s_rotate(poly, -angle, origin=(ox, oy))
    x0, y0, x1, y1 = rp.bounds
    out = []
    k = math.floor((y0 - oy) / spacing)
    y = oy + k * spacing
    while y <= y1:
        seg = LineString([(x0 - 1, y), (x1 + 1, y)]).intersection(rp)
        for part in getattr(seg, "geoms", [seg]):
            if isinstance(part, LineString) and part.length > 1:
                out.append(s_rotate(part, angle, origin=(ox, oy)))
        y += spacing
    return out


def hatch_floor(space, poly: Polygon, spec: PatternSpec, origin, layer="I-FLOR-PATT", k: float = 1.0):
    style, spacing, angle = spec
    angles = [angle, angle + 90] if style == "grid" else [angle]
    for a in angles:
        for ln in _pattern_lines(poly, spacing * k, a, origin):
            space.add_line(ln.coords[0], ln.coords[-1], dxfattribs={"layer": layer})


def draw_flooring(ctx: Ctx) -> None:
    finishes = {f.code: f for f in ctx.model.finishes}
    # platforms carry their own finish; voids have no floor
    cutouts = unary_union([ctx.room_net[r.id] for r in ctx.model.rooms
                           if r.kind != "room" and r.id in ctx.room_net])
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty or r.kind == "void":
            continue
        area = net.difference(cutouts) if r.kind == "room" and not cutouts.is_empty else net
        bx0, by0, _, _ = net.bounds
        for part in g.polygons(area):
            hatch_floor(ctx.msp, part, floor_pattern(finishes.get(r.floor_finish)), (bx0, by0))


# ---------------------------------------------------------------- AC units (HVAC coordination)

AC_LABELS = {
    "wall_mounted": "Wall-mounted split unit",
    "ceiling_cassette": "Ceiling cassette unit",
    "concealed_ducted": "Concealed ducted unit",
    "floor_standing": "Floor-standing unit",
}
AC_SIZES = {  # typical outline, mm (width, depth)
    "wall_mounted": (900, 300),
    "ceiling_cassette": (840, 840),
    "concealed_ducted": (1100, 700),
    "floor_standing": (500, 300),
}


def draw_ac_symbol(space, kind: str, x: float, y: float, rot: float = 0.0, k: float = 1.0,
                   layer: str = "M-HVAC-EQPM") -> None:
    w, d = AC_SIZES[kind]
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    T = lambda u, v: (x + (u * c - v * s) * k, y + (u * s + v * c) * k)  # noqa: E731
    box = lambda hw, hd, **a: _poly(space, [T(-hw, -hd), T(hw, -hd), T(hw, hd), T(-hw, hd)], layer, **a)  # noqa: E731
    line = lambda a, b: space.add_line(T(*a), T(*b), dxfattribs={"layer": layer})  # noqa: E731
    if kind == "wall_mounted":
        box(w / 2, d / 2)
        line((-w / 2 + 60, -d / 2 + 60), (w / 2 - 60, -d / 2 + 60))  # air outlet louvre (front, local -Y)
    elif kind == "ceiling_cassette":
        box(w / 2, d / 2)
        box(300, 300)
        for a, b in (((-260, 330), (260, 330)), ((-260, -330), (260, -330)),
                     ((330, -260), (330, 260)), ((-330, -260), (-330, 260))):
            line(a, b)
    elif kind == "concealed_ducted":
        box(w / 2, d / 2, linetype="DASHED", ltscale=max(k * 8, 0.2))
        line((-w / 2, -d / 2), (w / 2, d / 2))
    else:
        box(w / 2, d / 2)
        line((-w / 2, -d / 2), (w / 2, d / 2))
        line((-w / 2, d / 2), (w / 2, -d / 2))


def draw_ac_units(ctx: Ctx) -> None:
    for a in ctx.model.ac_units:
        draw_ac_symbol(ctx.msp, a.type, a.position.x, a.position.y, a.rotation)
        # tag below the unit
        tx, ty = a.position.x, a.position.y - ctx.h(7)
        tw, th = ctx.h(11), ctx.h(4)
        _rect(ctx.msp, tx - tw / 2, ty - th / 2, tx + tw / 2, ty + th / 2, "M-HVAC-IDEN")
        _text(ctx.msp, a.tag, (tx, ty), ctx.h(2.0), "M-HVAC-IDEN")
        # set-out dimensions from the nearest wall faces of its room
        net = ctx.room_net.get(a.room_id)
        if net is None or net.is_empty:
            continue
        bx0, by0, bx1, by1 = net.bounds
        cx, cy = a.position.x, a.position.y
        fx = bx0 if cx - bx0 <= bx1 - cx else bx1
        fy = by0 if cy - by0 <= by1 - cy else by1
        if abs(cx - fx) > 50:
            ctx.msp.add_linear_dim(base=(fx, cy + ctx.h(5)), p1=(fx, cy), p2=(cx, cy), angle=0,
                                   dimstyle="ADC-ARCH", dxfattribs={"layer": "M-HVAC-DIMS"}).render()
        if abs(cy - fy) > 50:
            ctx.msp.add_linear_dim(base=(cx + ctx.h(5), fy), p1=(cx, fy), p2=(cx, cy), angle=90,
                                   dimstyle="ADC-ARCH", dxfattribs={"layer": "M-HVAC-DIMS"}).render()


# ---------------------------------------------------------------- paper space: frame, title block, legends

def _title_block(ctx: Ctx, ps: Paperspace, spec: SheetSpec, short: str, title: str) -> None:
    """Fields required on every sheet by the ADC drawing register."""
    p = ctx.model.project
    L = "G-ANNO-TTLB"
    x0, y0, x1, y1 = FRAME
    sx, w = STRIP_X, x1 - STRIP_X
    _rect(ps, 5, 5, PAPER_W - 5, PAPER_H - 5, L, lineweight=13)
    _rect(ps, x0, y0, x1, y1, L, lineweight=50)
    ps.add_line((sx, y0), (sx, y1), dxfattribs={"layer": L, "lineweight": 35})

    def hline(y):
        ps.add_line((sx, y), (x1, y), dxfattribs={"layer": L})

    def vline(x, ya, yb):
        ps.add_line((x, ya), (x, yb), dxfattribs={"layer": L})

    def field_(xa, ya, xb, yb, label, value, h=2.2):
        _text(ps, label.upper(), (xa + 1.5, yb - 2.2), 1.4, L, ALIGN.LEFT)
        if value:
            _text(ps, _fit(str(value), xb - xa - 3, h), (xa + 1.5, ya + 2.0), h, L, ALIGN.LEFT)

    # logo
    if ctx.logo_def is not None:
        lx0, ly0, lx1, ly1 = logo_rect()
        ps.add_image(ctx.logo_def, insert=(lx0, ly0), size_in_units=(lx1 - lx0, ly1 - ly0), dxfattribs={"layer": L})
    hline(240)

    # north point (plans only) and the do-not-scale note
    if spec.north:
        nx, ny = sx + 10, 229
        ps.add_circle((nx, ny), 5.5, dxfattribs={"layer": L})
        _poly(ps, [(nx, ny + 5.5), (nx - 2.8, ny - 3.5), (nx, ny - 1.8), (nx + 2.8, ny - 3.5)], L)
        _text(ps, "N", (nx, ny + 7.2), 2.0, L)
    _text(ps, "DO NOT SCALE THIS DRAWING", (sx + (21 if spec.north else 3), 231), 1.9, L, ALIGN.LEFT)
    _text(ps, "Use written dimensions only.", (sx + (21 if spec.north else 3), 227), 1.5, L, ALIGN.LEFT)
    hline(219)

    # (legend area 219 -> 150 is filled by the sheet's legend function)

    # notes
    hline(150)
    notes = ["NOTES",
             "1. Dimensions in mm, levels in m, unless noted.",
             "2. Verify all dimensions on site before fabrication.",
             "3. Read with the specification and schedules.",
             "4. Generated with AI-assisted extraction from images;",
             "    to be checked by a qualified professional."]
    if spec.note:
        notes.append(f"5. {spec.note}")
    for i, line in enumerate(notes):
        _text(ps, line, (sx + 2, 147 - i * 3.3), 1.5 if i else 1.8, L, ALIGN.LEFT)
    hline(124)

    # revision history (latest four)
    _text(ps, "REV", (sx + 1.5, 121.5), 1.4, L, ALIGN.LEFT)
    _text(ps, "DATE", (sx + 9, 121.5), 1.4, L, ALIGN.LEFT)
    _text(ps, "DESCRIPTION", (sx + 26, 121.5), 1.4, L, ALIGN.LEFT)
    _text(ps, "BY", (x1 - 7, 121.5), 1.4, L, ALIGN.LEFT)
    revs = p.revisions[-4:] if p.revisions else []
    for i, r in enumerate(revs):
        yy = 117 - i * 3.6
        _text(ps, r.rev, (sx + 1.5, yy), 1.6, L, ALIGN.LEFT)
        _text(ps, r.date, (sx + 9, yy), 1.4, L, ALIGN.LEFT)
        _text(ps, _fit(r.description, 30, 1.4), (sx + 26, yy), 1.4, L, ALIGN.LEFT)
        _text(ps, r.by, (x1 - 7, yy), 1.4, L, ALIGN.LEFT)
    hline(101)

    # project
    field_(sx, 88, x1, 101, "Project", p.project_name, 2.4)
    _text(ps, f"Code: {p.project_code or '-'}", (sx + 1.5, 86.5), 1.7, L, ALIGN.LEFT)
    _text(ps, _fit(f"Client: {p.client or '-'}", w - 3, 1.7), (sx + 1.5, 83), 1.7, L, ALIGN.LEFT)
    _text(ps, _fit(f"Location: {p.location or '-'}", w - 3, 1.7), (sx + 1.5, 79.5), 1.7, L, ALIGN.LEFT)
    hline(76)
    # sheet title
    _text(ps, "SHEET TITLE", (sx + 1.5, 73.8), 1.4, L, ALIGN.LEFT)
    ps.add_mtext(title.upper(), dxfattribs={"layer": L, "style": "ADC", "char_height": 2.6,
                                            "width": w - 4}).set_location((sx + 1.5, 71.5))
    hline(62)
    # stage | scale | paper
    c1, c2 = sx + w * 0.42, sx + w * 0.72
    vline(c1, 50, 62)
    vline(c2, 50, 62)
    field_(sx, 50, c1, 62, "Stage", f"{p.stage}", 2.6)
    _text(ps, STAGE_NAMES[p.stage], (sx + 11, 52), 1.3, L, ALIGN.LEFT)
    field_(c1, 50, c2, 62, "Scale", f"1:{ctx.scale}" if spec.north else "NTS", 2.4)
    field_(c2, 50, x1, 62, "Paper", PAPER_NAME, 2.4)
    hline(50)
    # drawn | checked | approved | date
    d1, d2, d3 = sx + w * 0.22, sx + w * 0.44, sx + w * 0.66
    for xa in (d1, d2, d3):
        vline(xa, 38, 50)
    field_(sx, 38, d1, 50, "Drawn", p.drawn_by)
    field_(d1, 38, d2, 50, "Checked", p.checked_by)
    field_(d2, 38, d3, 50, "Approved", p.approved_by)
    field_(d3, 38, x1, 50, "Date", p.date, 1.9)
    hline(38)
    # sheet number + revision
    vline(x1 - 14, y0, 38)
    _text(ps, "SHEET NO.", (sx + 1.5, 35.8), 1.4, L, ALIGN.LEFT)
    _text(ps, short, (sx + 1.5, 24), 5.0, L, ALIGN.MIDDLE_LEFT)
    _text(ps, sheet_number(ctx.model, short), (sx + 1.5, 13.5), 1.6, L, ALIGN.LEFT)
    _text(ps, "REV", (x1 - 12.5, 35.8), 1.4, L, ALIGN.LEFT)
    _text(ps, p.revision, (x1 - 7, 24), 5.0, L)

    # copyright line in the bottom margin
    year = (p.date or "")[:4] if re.match(r"\d{4}", p.date or "") else ""
    _text(ps, f"© {year} {p.company_name}. All rights reserved. This drawing and its design are the property of "
              f"{p.company_name} and may not be copied or used without written permission.".replace("©  ", "© "),
          (x0, 7.0), 1.5, L, ALIGN.LEFT)


def logo_rect() -> tuple[float, float, float, float]:
    """Logo placement on paper, keeping the image's aspect ratio inside LOGO_BOX."""
    bx0, by0, bx1, by1 = LOGO_BOX
    aspect = LOGO_PX[0] / LOGO_PX[1]
    w = min(bx1 - bx0, (by1 - by0) * aspect)
    h = w / aspect
    x0, y0 = (bx0 + bx1 - w) / 2, by0 + (by1 - by0 - h) / 2
    return x0, y0, x0 + w, y0 + h


def _drawing_title(ctx: Ctx, ps: Paperspace, short: str, title: str) -> None:
    L = "G-ANNO-TTLB"
    x, y = PLAN_AREA[0] + 8, 24
    ps.add_circle((x, y), 6, dxfattribs={"layer": L, "lineweight": 35})
    ps.add_line((x - 6, y), (x + 6, y), dxfattribs={"layer": L})
    _text(ps, "1", (x, y + 3), 3, L)
    _text(ps, short, (x, y - 3), 1.6, L)
    _text(ps, title.upper(), (x + 10, y + 1), 3.6, L, ALIGN.LEFT)
    ps.add_line((x + 10, y - 1.5), (x + 160, y - 1.5), dxfattribs={"layer": L, "lineweight": 50})
    lv = ctx.model.level(ctx.level_id)
    _text(ps, f"SCALE 1:{ctx.scale} @ {PAPER_NAME}   ·   {lv.name.upper()}   ·   {level_text(lv.ffl)}",
          (x + 10, y - 5), 2.0, L, ALIGN.LEFT)

    # scale bar: five segments of a round length
    seg_m = next(s for s in (0.25, 0.5, 1, 2, 5, 10, 20) if 5 * s * 1000 / ctx.scale >= 40)
    seg = seg_m * 1000 / ctx.scale
    bx, by = 240.0, 20.0
    for i in range(5):
        _rect(ps, bx + i * seg, by, bx + (i + 1) * seg, by + 2, L)
        if i % 2 == 0:
            h = ps.add_hatch(color=7, dxfattribs={"layer": L})
            h.paths.add_polyline_path([(bx + i * seg, by), (bx + (i + 1) * seg, by),
                                       (bx + (i + 1) * seg, by + 2), (bx + i * seg, by + 2)])
        _text(ps, f"{i * seg_m:g}", (bx + i * seg, by - 2.5), 1.8, L)
    _text(ps, f"{5 * seg_m:g} m", (bx + 5 * seg + 1, by - 2.5), 1.8, L, ALIGN.MIDDLE_LEFT)


def _viewport(ctx: Ctx, ps: Paperspace, spec: SheetSpec) -> None:
    x0, y0, x1, y1 = g.extents(ctx.model)
    ax0, ay0, ax1, ay1 = PLAN_AREA
    vp = ps.add_viewport(
        center=((ax0 + ax1) / 2, (ay0 + ay1) / 2),
        size=(ax1 - ax0, ay1 - ay0),
        view_center_point=((x0 + x1) / 2, (y0 + y1) / 2),
        view_height=(ay1 - ay0) * ctx.scale,
        dxfattribs={"layer": "G-ANNO-NPLT"},
    )
    vp.frozen_layers = sorted(set(LAYERS) - spec.show - {"G-ANNO-TTLB", "G-ANNO-TEXT", "G-ANNO-NPLT"})
    vp.dxf.flags = vp.dxf.flags | 16384  # display locked


def _legend_box(ps, title: str, top: float) -> float:
    _text(ps, title, (STRIP_X + 3, top), 2.0, "G-ANNO-TEXT", ALIGN.LEFT)
    return top - 5.5


def legend_ceiling(ctx: Ctx, ps: Paperspace, top: float) -> None:
    kinds = sorted({c.kind for c in ctx.model.ceiling_fixtures}, key=list(FIXTURE_LABELS).index)
    y = _legend_box(ps, "CEILING LEGEND", top)
    for kind in kinds:
        draw_fixture(ps, kind, STRIP_X + 7, y, k=0.012, length=600, layer="G-ANNO-TEXT", text_h=1.0)
        _text(ps, FIXTURE_LABELS[kind], (STRIP_X + 14, y), 1.7, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 5
    for f in [f for f in ctx.model.finishes if f.category == "ceiling"]:
        _text(ps, f"{f.code}  {f.description}"[:44], (STRIP_X + 3, y), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 4


def legend_floor(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "FLOOR FINISHES", top)
    used = {r.floor_finish for r in ctx.model.rooms}
    for f in [f for f in ctx.model.finishes if f.category == "floor" and f.code in used]:
        box = Polygon([(STRIP_X + 3, y - 3), (STRIP_X + 13, y - 3), (STRIP_X + 13, y + 3), (STRIP_X + 3, y + 3)])
        _poly(ps, list(box.exterior.coords)[:-1], "G-ANNO-TEXT")
        style, spacing, angle = floor_pattern(f)
        hatch_floor(ps, box, (style, min(max(spacing / ctx.scale, 1.0), 2.5), angle), (STRIP_X + 3, y - 3), "G-ANNO-TEXT")
        _text(ps, f.code, (STRIP_X + 15, y + 1.2), 2.0, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        _text(ps, f.description[:34], (STRIP_X + 15, y - 1.8), 1.5, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 9


def legend_ga(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "AREA SUMMARY", top)
    rooms = [r for r in ctx.model.rooms if r.kind == "room"]
    total = sum(ctx.room_net[r.id].area for r in rooms if r.id in ctx.room_net) / 1e6
    for r in rooms[:11]:
        a = ctx.room_net[r.id].area / 1e6 if r.id in ctx.room_net else 0
        _text(ps, _fit(f"{r.number} {r.name}".strip(), 50, 1.7), (STRIP_X + 3, y), 1.7, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        _text(ps, f"{a:.1f} m²", (FRAME[2] - 3, y), 1.7, "G-ANNO-TEXT", ALIGN.MIDDLE_RIGHT)
        y -= 4
    if len(rooms) > 11:
        _text(ps, "(see ID-901 for all rooms)", (STRIP_X + 3, y), 1.5, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 4
    ps.add_line((STRIP_X + 3, y + 2), (FRAME[2] - 3, y + 2), dxfattribs={"layer": "G-ANNO-TEXT"})
    _text(ps, "TOTAL NET AREA", (STRIP_X + 3, y - 1), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
    _text(ps, f"{total:.1f} m²", (FRAME[2] - 3, y - 1), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_RIGHT)


def legend_furniture(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "FF&E TAGS", top)
    lines = ["F01 = item tag, see ID-913 FF&E schedule.", "Tag suffix shows the item status:",
             "  (none) New      -R  Reuse existing", "  -M  Modify       -RL Relocate", "  -X  Remove"]
    for line in lines:
        _text(ps, line, (STRIP_X + 3, y), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 4


def legend_doors(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "DOOR & OPENING TAGS", top)
    ps.add_circle((STRIP_X + 6, y), 2.4, dxfattribs={"layer": "G-ANNO-TEXT"})
    _text(ps, "D01", (STRIP_X + 6, y), 1.3, "G-ANNO-TEXT")
    _text(ps, "Door / opening, see ID-906", (STRIP_X + 11, y), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
    y -= 6.5
    _poly(ps, [(STRIP_X + 3.6, y), (STRIP_X + 6, y - 2.4), (STRIP_X + 8.4, y), (STRIP_X + 6, y + 2.4)], "G-ANNO-TEXT")
    _text(ps, "W01", (STRIP_X + 6, y), 1.2, "G-ANNO-TEXT")
    _text(ps, "Window, see ID-906", (STRIP_X + 11, y), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)


def legend_hvac(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "HVAC LEGEND", top)
    for kind in [k for k in AC_LABELS if any(a.type == k for a in ctx.model.ac_units)]:
        draw_ac_symbol(ps, kind, STRIP_X + 8, y, k=0.007, layer="G-ANNO-TEXT")
        _text(ps, AC_LABELS[kind], (STRIP_X + 15, y), 1.7, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 6
    _text(ps, "Capacities and routes: see ID-917 / MEP.", (STRIP_X + 3, y), 1.5, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)


# ---------------------------------------------------------------- schedules (Series 09)

def _room_label(ctx: Ctx, room_id: str) -> str:
    r = next((r for r in ctx.model.rooms if r.id == room_id), None)
    return f"{r.number} {r.name}".strip() if r else "-"


def _rooms_beside(ctx: Ctx, w: Wall, o: Opening) -> str:
    names = []
    for side in (1, -1):
        px, py = g.along(w, o.offset + o.width / 2, side * (w.thickness / 2 + 150))
        for r in ctx.model.rooms:
            net = ctx.room_net.get(r.id)
            if r.kind == "room" and net is not None and net.buffer(100).contains(SPoint(px, py)):
                names.append(f"{r.number} {r.name}".strip())
                break
        else:
            names.append("Outside" if w.kind == "exterior" else "-")
    return " / ".join(names)


Table = tuple[str, list[float], list[str], list[list[str]]]


def _multi(ctx: Ctx) -> bool:
    return len(ctx.model.level_ids()) > 1


def schedule_sheets_spec(ctx: Ctx) -> list[tuple[str, str, list[Table]]]:
    """(register number, title, tables) for each schedule sheet that has content."""
    m = ctx.model
    walls = {w.id: w for w in m.walls}
    lv_col = _multi(ctx)
    rooms = [r for r in m.rooms if r.kind != "void"]
    area = lambda rid: ctx.room_net[rid].area / 1e6 if rid in ctx.room_net else 0.0  # noqa: E731

    sheets: list[tuple[str, str, list[Table]]] = []

    sheets.append(("ID-901", "Room finishes schedule", [(
        "ROOM FINISHES SCHEDULE",
        ([14] if lv_col else []) + [14, 52, 22, 20, 30, 20, 20, 18, 60],
        (["Level"] if lv_col else []) + ["No.", "Room", "Area m²", "Floor", "Skirting", "Walls", "Ceiling", "CH", "Remarks"],
        [([r.level] if lv_col else []) + [
            r.number, r.name + (" (platform)" if r.kind == "platform" else ""),
            f"{area(r.id):.2f}", r.floor_finish or "TBC", r.skirting or "-", r.wall_finish or "-",
            r.ceiling_finish or ("-" if r.kind == "platform" else "TBC"),
            "-" if r.kind == "platform" else f"{r.ceiling_height:.0f}",
            level_text(r.floor_level) if r.floor_level else ""]
         for r in sorted(rooms, key=lambda r: (m.level_ids().index(r.level), r.number, r.name))])]))

    def used_in(code):
        return ", ".join(sorted({f"{r.number or r.name}" for r in m.rooms
                                 if code in (r.floor_finish, r.ceiling_finish, r.wall_finish, r.skirting)}))

    sheets.append(("ID-902", "Material & finishes schedule", [(
        "MATERIAL & FINISHES SCHEDULE", [18, 18, 72, 64, 60, 88],
        ["Code", "Type", "Description", "Material / colour / size", "Location", "Supplier / notes"],
        [[f.code, f.category.title(), f.description, f.material, used_in(f.code), f.notes] for f in m.finishes])]))

    ceil_rows = []
    for f in [f for f in m.finishes if f.category == "ceiling"]:
        rs = [r for r in m.rooms if r.ceiling_finish == f.code and r.kind == "room"]
        if rs:
            heights = sorted({f"{r.ceiling_height:.0f}" for r in rs})
            ceil_rows.append([f.code, f.description, f.material, ", ".join(heights), f"{sum(area(r.id) for r in rs):.2f}",
                              ", ".join(r.number or r.name for r in rs)])
    sheets.append(("ID-905", "Ceiling schedule", [(
        "CEILING SCHEDULE", [18, 70, 60, 34, 22, 116],
        ["Code", "Type", "Finish", "Level (CH mm)", "Area m²", "Rooms"], ceil_rows)]))

    kind_label = {"door": "Single swing door", "double_door": "Double swing door",
                  "sliding_door": "Sliding door", "window": "Window", "opening": "Opening"}
    doors = sorted([o for o in m.openings if o.kind != "window"], key=lambda o: o.tag)
    windows = sorted([o for o in m.openings if o.kind == "window"], key=lambda o: o.tag)
    sheets.append(("ID-906", "Door schedule", [
        ("DOOR SCHEDULE", [16, 36, 26, 22, 34, 24, 98],
         ["Tag", "Type", "Size WxH", "Status", "Leaf / frame / finish", "Fire rating", "Location"],
         [[o.tag, kind_label[o.kind], f"{o.width:.0f}x{o.height:.0f}", STATUS_LABEL[o.status], "TBC", "TBC",
           _rooms_beside(ctx, walls[o.wall_id], o)] for o in doors]),
        ("WINDOW SCHEDULE", [16, 36, 26, 22, 18, 138],
         ["Tag", "Type", "Size WxH", "Status", "Sill", "Location"],
         [[o.tag, kind_label[o.kind], f"{o.width:.0f}x{o.height:.0f}", STATUS_LABEL[o.status], f"{o.sill_height:.0f}",
           _rooms_beside(ctx, walls[o.wall_id], o)] for o in windows]),
    ]))

    # identical items are grouped into one line with a quantity
    groups: dict[tuple, list[Furniture]] = {}
    for f in sorted(m.furniture, key=lambda f: f.tag):
        key = (f.name, f.category, round(f.width), round(f.depth), f.finish, f.status)
        groups.setdefault(key, []).append(f)
    sheets.append(("ID-913", "FF&E schedule - furniture", [(
        "FF&E SCHEDULE - FURNITURE", [26, 52, 26, 60, 12, 22, 58, 32],
        ["Tag(s)", "Item", "Size WxD", "Material / finish", "Qty", "Status", "Location", "Supplier / source"],
        [[", ".join(f.tag for f in fs) if len(fs) < 4 else f"{fs[0].tag}-{fs[-1].tag}", fs[0].name,
          f"{fs[0].width:.0f}x{fs[0].depth:.0f}", fs[0].finish or "TBC", str(len(fs)), STATUS_LABEL[fs[0].status],
          ", ".join(sorted({_room_label(ctx, f.room_id) for f in fs})), fs[0].source]
         for fs in groups.values()])]))

    sheets.append(("ID-917", "HVAC equipment schedule", [(
        "HVAC EQUIPMENT SCHEDULE (SPLIT UNITS)", [16, 12, 46, 18, 40, 30, 22, 66, 18],
        ["Tag", "Level", "Room", "Area m²", "Indoor type", "Capacity", "Mtg ht AFFL", "Outdoor unit location", "Status"],
        [[a.tag, a.level, _room_label(ctx, a.room_id), f"{area(a.room_id):.1f}" if a.room_id else "-",
          AC_LABELS[a.type], a.capacity or "TBC",
          f"{a.mounting_height:.0f}" if a.mounting_height else "-", a.outdoor_location or "TBC", STATUS_LABEL[a.status]]
         for a in sorted(m.ac_units, key=lambda a: a.tag)])]))

    return [(num, title, [t for t in tables if t[3]]) for num, title, tables in sheets
            if any(t[3] for t in tables)]


def _fit(text: str, width_mm: float, h: float) -> str:
    n = max(1, int(width_mm / (h * 0.62)))
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- assembly

def _new_sheet(ctx: Ctx, spec: SheetSpec, short: str, title: str) -> Paperspace:
    ps = ctx.doc.layouts.new(f"{short} {title}"[:255])
    ps.page_setup(size=(PAPER_W, PAPER_H), margins=(0, 0, 0, 0), units="mm")
    _title_block(ctx, ps, spec, short, title)
    return ps


def _schedule_sheets(ctx: Ctx) -> list[str]:
    row_h, head_h, h = 5.0, 6.0, 1.8
    x_left, y_top, y_min = FRAME[0] + 6, FRAME[3] - 8, FRAME[1] + 8
    names = []
    for number, title, tables in schedule_sheets_spec(ctx):
        spec = SheetSpec(number, title, north=False)
        page = 0
        sheet = None
        y = y_top

        def new():
            nonlocal sheet, y, page
            page += 1
            short = number if page == 1 else f"{number}.{page}"
            sheet = _new_sheet(ctx, spec, short, title if page == 1 else f"{title} (cont.)")
            names.append(sheet.name)
            y = y_top

        new()
        for t_title, widths, headers, rows in tables:
            if y - (8 + head_h + 2 * row_h) < y_min:
                new()
            _text(sheet, t_title, (x_left, y), 3.0, "G-ANNO-TEXT", ALIGN.LEFT)
            y -= 4

            def header():
                nonlocal y
                x = x_left
                _rect(sheet, x_left, y - head_h, x_left + sum(widths), y, "G-ANNO-TEXT", lineweight=25)
                for wd, hd in zip(widths, headers):
                    _text(sheet, _fit(hd.upper(), wd - 2, h), (x + 1.5, y - head_h / 2), h, "G-ANNO-TEXT",
                          ALIGN.MIDDLE_LEFT)
                    x += wd
                y -= head_h

            header()
            for row in rows:
                if y - row_h < y_min:
                    new()
                    _text(sheet, f"{t_title} (CONT.)", (x_left, y), 3.0, "G-ANNO-TEXT", ALIGN.LEFT)
                    y -= 4
                    header()
                x = x_left
                _rect(sheet, x_left, y - row_h, x_left + sum(widths), y, "G-ANNO-TEXT")
                for wd, val in zip(widths, row):
                    _text(sheet, _fit(str(val or ""), wd - 3, h), (x + 1.5, y - row_h / 2), h, "G-ANNO-TEXT",
                          ALIGN.MIDDLE_LEFT)
                    x += wd
                y -= row_h
            y -= 10
    return names


ALL = frozenset(STAGE_NAMES)
FROM_SD = frozenset({"SD", "DD", "AP", "IFT", "IFC", "AB"})
FROM_DD = frozenset({"DD", "AP", "IFT", "IFC", "AB"})
SCHEDULE_STAGES = frozenset({"SD", "DD", "IFT", "IFC", "AB"})  # register: Series 09 not issued at CD or AP

BASE = {"A-WALL", "A-WALL-PATT", "G-ROOM-NAME", "A-FLOR-OPEN", "A-FLOR-LEVL"}
PLAN_SHEETS = [
    SheetSpec("ID-201", "General arrangement plan",
              BASE | {"A-DOOR", "A-GLAZ", "A-ANNO-DIMS", "A-AREA-IDEN"}, legend_ga, FROM_SD),
    SheetSpec("ID-202", "Furniture layout plan",
              BASE | {"A-DOOR", "A-GLAZ", "I-FURN", "I-FURN-IDEN"}, legend_furniture, ALL, needs="furniture"),
    SheetSpec("ID-203", "Dimensioned layout plan",
              BASE | {"A-DOOR", "A-GLAZ", "A-ANNO-DIMS", "A-ANNO-DIMS-INTR", "I-FURN"}, None, FROM_SD),
    SheetSpec("ID-204", "Flooring finish plan",
              BASE | {"A-DOOR-HEAD", "A-GLAZ", "I-FLOR-PATT", "I-FLOR-IDEN"}, legend_floor, FROM_SD),
    SheetSpec("ID-208", "Door & opening plan",
              BASE | {"A-DOOR", "A-GLAZ", "A-DOOR-IDEN"}, legend_doors, FROM_SD, needs="openings"),
    SheetSpec("ID-301", "Reflected ceiling plan (RCP)",
              BASE | {"A-DOOR-HEAD", "A-GLAZ", "I-CLNG", "I-CLNG-IDEN"}, legend_ceiling, FROM_SD,
              needs="ceiling_fixtures"),
    SheetSpec("ID-405", "HVAC coordination plan",
              BASE | {"A-DOOR-HEAD", "A-GLAZ", "M-HVAC-EQPM", "M-HVAC-IDEN", "M-HVAC-DIMS"}, legend_hvac, FROM_DD,
              needs="ac_units", note="FOR COORDINATION ONLY - design intent for the MEP engineer."),
]


def _sheet_wanted(spec: SheetSpec, model: BuildingModel, stage: str) -> bool:
    if stage not in spec.stages:
        return False
    if stage == "CD" and spec.number != "ID-202":  # register: concept client deck carries ID-202 only
        return False
    return not spec.needs or bool(getattr(model, spec.needs))


def build_dxf(model: BuildingModel) -> tuple[ezdxf.document.Drawing, list[str], int]:
    """Returns the document, the layout names in sheet order and the plan scale."""
    levels = model.level_ids()
    parts = [g.level_model(model, lv) for lv in levels]
    scale = max(choose_scale(p) for p in parts)
    doc = ezdxf.new("R2018", setup=True)
    _setup(doc, scale)
    msp = doc.modelspace()
    # The DXF references the logo next to it (the CAD download ships both files).
    logo_def = doc.add_image_def(filename=LOGO_FILE.name, size_in_pixel=LOGO_PX) if LOGO_FILE.exists() else None

    # lay the levels out side by side in model space
    ctxs: list[Ctx] = []
    cursor = 0.0
    for lv, part in zip(levels, parts):
        x0, _, x1, _ = g.extents(part)
        part = g.translate(part, cursor - x0)
        cursor += (x1 - x0) + LEVEL_GAP
        geo = g.wall_geometry(part)
        ctx = Ctx(model=part, doc=doc, msp=msp, scale=scale, geo=geo, level_id=lv,
                  suffix=f".{lv}" if len(levels) > 1 else "", logo_def=logo_def)
        ctx.room_net = {r.id: g.room_net_polygon(r.polygon, geo.solid) for r in part.rooms}
        for draw in (draw_flooring, draw_walls, draw_openings, draw_levels_and_voids, draw_room_tags,
                     draw_dimensions, draw_furniture, draw_ceiling, draw_ac_units):
            draw(ctx)
        ctxs.append(ctx)

    stage = model.project.stage
    names = []
    for spec in PLAN_SHEETS:  # register order: all levels of one sheet type together
        for ctx in ctxs:
            if not _sheet_wanted(spec, ctx.model, stage):
                continue
            short = f"{spec.number}{ctx.suffix}"
            title = spec.title
            ps = _new_sheet(ctx, spec, short, title)
            _viewport(ctx, ps, spec)
            _drawing_title(ctx, ps, short, f"{ctx.model.level(ctx.level_id).name} - {title}"
                           if len(levels) > 1 else title)
            if spec.legend:
                spec.legend(ctx, ps, LEGEND_TOP)
            names.append(ps.name)

    if stage in SCHEDULE_STAGES:
        merged = model.model_copy(update={
            k: [x for c in ctxs for x in getattr(c.model, k)]
            for k in ("walls", "openings", "rooms", "furniture", "ceiling_fixtures", "ac_units")})
        sctx = Ctx(model=merged, doc=doc, msp=msp, scale=scale, geo=ctxs[0].geo,
                   room_net={k: v for c in ctxs for k, v in c.room_net.items()}, logo_def=logo_def)
        names += _schedule_sheets(sctx)

    if "Layout1" in doc.layouts:  # ezdxf's default empty sheet
        doc.layouts.delete("Layout1")
    return doc, names, scale
