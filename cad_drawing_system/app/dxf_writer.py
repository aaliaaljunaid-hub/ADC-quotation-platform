"""Generate a DXF drawing set from a BuildingModel.

Everything is drawn once in model space on AIA-style layers. Each sheet is a
paper-space layout (A3 landscape) with a scaled viewport that freezes the
layers that sheet should not show, plus title block, legend and scale bar.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable

import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.layouts import Layout, Paperspace
from shapely.affinity import rotate as s_rotate
from shapely.geometry import LineString, Polygon
from shapely.ops import polylabel

from . import geometry as g
from .schema import BuildingModel, Finish, Furniture, Opening, Wall

# ---------------------------------------------------------------- sheet constants (mm on paper)

PAPER_W, PAPER_H = 420.0, 297.0  # A3 landscape
FRAME = (20.0, 10.0, 410.0, 287.0)  # x0, y0, x1, y1 (wider left margin for binding)
STRIP_X = 335.0  # title strip starts here
PLAN_AREA = (25.0, 34.0, 330.0, 282.0)  # where the viewport goes
SCALES = [20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500]
DIM_MARGIN = 30.0  # paper mm needed outside the plan for dimension chains

ALIGN = TextEntityAlignment

# name: (colour, lineweight in 1/100 mm, linetype)
LAYERS = {
    "A-WALL": (7, 50, "Continuous"),
    "A-WALL-PATT": (8, 0, "Continuous"),
    "A-DOOR": (7, 25, "Continuous"),
    "A-DOOR-HEAD": (7, 18, "Continuous"),
    "A-GLAZ": (7, 18, "Continuous"),
    "A-DOOR-IDEN": (7, 18, "Continuous"),
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
    "G-ANNO-TTLB": (7, 25, "Continuous"),
    "G-ANNO-TEXT": (7, 18, "Continuous"),
    "G-ANNO-NPLT": (8, 0, "Continuous"),
}

WALL_FILL = {"exterior": 8, "interior": 8, "partition": 9}


@dataclass
class SheetSpec:
    number: str
    title: str
    show: set[str]
    legend: Callable[["Ctx", Paperspace, float], None] | None = None


@dataclass
class Ctx:
    model: BuildingModel
    doc: ezdxf.document.Drawing
    msp: Layout
    scale: int
    geo: g.WallGeometry
    room_net: dict[str, Polygon] = field(default_factory=dict)

    def h(self, paper_mm: float) -> float:
        """Model-space size that prints at `paper_mm` on the sheet."""
        return paper_mm * self.scale


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


def draw_room_tags(ctx: Ctx) -> None:
    finishes = {f.code: f for f in ctx.model.finishes}
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty:
            continue
        x, y = _label_point(net)
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
        if net is None or net.is_empty:
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
            _text(ctx.msp, f.tag, (f.center.x, f.center.y), ctx.h(1.6), "I-FURN-IDEN")


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
    for r in ctx.model.rooms:
        net = ctx.room_net.get(r.id)
        if net is None or net.is_empty:
            continue
        bx0, by0, _, _ = net.bounds
        hatch_floor(ctx.msp, net, floor_pattern(finishes.get(r.floor_finish)), (bx0, by0))


# ---------------------------------------------------------------- paper space: frame, title block, legends

def _title_block(ctx: Ctx, ps: Paperspace, spec: SheetSpec) -> None:
    p = ctx.model.project
    L = "G-ANNO-TTLB"
    x0, y0, x1, y1 = FRAME
    _rect(ps, 5, 5, PAPER_W - 5, PAPER_H - 5, L, lineweight=13)
    _rect(ps, x0, y0, x1, y1, L, lineweight=50)
    ps.add_line((STRIP_X, y0), (STRIP_X, y1), dxfattribs={"layer": L, "lineweight": 35})
    sx = STRIP_X + 3

    def cell(ya, yb, label, value, h=2.5, xa=STRIP_X, xb=x1):
        ps.add_line((xa, yb), (xb, yb), dxfattribs={"layer": L})
        _text(ps, label.upper(), (xa + 2, yb - 2.5), 1.5, L, ALIGN.LEFT)
        if value:
            _text(ps, value, (xa + 2, ya + 2.5), h, L, ALIGN.LEFT)

    xm = (STRIP_X + x1) / 2
    cell(y0, y0 + 16, "Drawing no.", spec.number, 5.0, xb=xm)
    cell(y0, y0 + 16, "Revision", p.revision, 5.0, xa=xm)
    ps.add_line((xm, y0), (xm, y0 + 32), dxfattribs={"layer": L})
    cell(y0 + 16, y0 + 32, "Scale @ A3", f"1:{ctx.scale}", 3.0, xb=xm)
    cell(y0 + 16, y0 + 32, "Date", p.date, 3.0, xa=xm)
    ps.add_line((xm, y0 + 32), (xm, y0 + 44), dxfattribs={"layer": L})
    cell(y0 + 32, y0 + 44, "Drawn", p.drawn_by, 2.5, xb=xm)
    cell(y0 + 32, y0 + 44, "Checked", p.checked_by, 2.5, xa=xm)
    cell(y0 + 44, y0 + 62, "Drawing title", "", 2.5)
    ps.add_mtext(spec.title.upper(), dxfattribs={"layer": L, "style": "ADC", "char_height": 3.2,
                                                  "width": x1 - STRIP_X - 4}).set_location((sx, y0 + 57.5))
    cell(y0 + 62, y0 + 92, "Project", "", 2.5)
    proj = "\\P".join(v for v in (p.project_name, p.client and f"Client: {p.client}", p.location,
                                    p.project_number and f"Job no. {p.project_number}") if v)
    ps.add_mtext(proj, dxfattribs={"layer": L, "style": "ADC", "char_height": 2.5,
                                   "width": x1 - STRIP_X - 4}).set_location((sx, y0 + 87.5))
    cell(y0 + 92, y0 + 104, "Status", p.status, 1.9)
    cell(y0 + 104, y0 + 118, "", p.company_name, 3.5)

    notes = ("NOTES\\P1. All dimensions in millimetres unless noted.\\P2. Do not scale from this drawing.\\P"
             "3. Verify all dimensions on site before fabrication or construction.\\P"
             "4. Geometry was extracted from images with AI assistance and must be checked "
             "and approved by a qualified professional before use.")
    ps.add_mtext(notes, dxfattribs={"layer": L, "style": "ADC", "char_height": 1.6,
                                    "width": x1 - STRIP_X - 6}).set_location((sx, y0 + 145))

    # north arrow (plan north assumed up)
    nx, ny = x1 - 12, y1 - 14
    ps.add_circle((nx, ny), 6, dxfattribs={"layer": L})
    _poly(ps, [(nx, ny + 6), (nx - 3, ny - 4), (nx, ny - 2), (nx + 3, ny - 4)], L)
    _text(ps, "N", (nx, ny + 9), 2.5, L)


def _drawing_title(ctx: Ctx, ps: Paperspace, spec: SheetSpec) -> None:
    L = "G-ANNO-TTLB"
    x, y = PLAN_AREA[0] + 8, 24
    ps.add_circle((x, y), 6, dxfattribs={"layer": L, "lineweight": 35})
    ps.add_line((x - 6, y), (x + 6, y), dxfattribs={"layer": L})
    _text(ps, "1", (x, y + 3), 3, L)
    _text(ps, spec.number, (x, y - 3), 2, L)
    _text(ps, spec.title.upper(), (x + 10, y + 1), 4, L, ALIGN.LEFT)
    ps.add_line((x + 10, y - 1.5), (x + 140, y - 1.5), dxfattribs={"layer": L, "lineweight": 50})
    _text(ps, f"SCALE 1:{ctx.scale} @ A3", (x + 10, y - 5), 2.2, L, ALIGN.LEFT)

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
    _text(ps, title, (STRIP_X + 3, top), 2.2, "G-ANNO-TEXT", ALIGN.LEFT)
    return top - 6


def legend_ceiling(ctx: Ctx, ps: Paperspace, top: float) -> None:
    kinds = sorted({c.kind for c in ctx.model.ceiling_fixtures}, key=list(FIXTURE_LABELS).index)
    y = _legend_box(ps, "CEILING LEGEND", top)
    for kind in kinds:
        draw_fixture(ps, kind, STRIP_X + 7, y, k=0.012, length=600, layer="G-ANNO-TEXT", text_h=1.0)
        _text(ps, FIXTURE_LABELS[kind], (STRIP_X + 14, y), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 6
    for f in [f for f in ctx.model.finishes if f.category == "ceiling"]:
        _text(ps, f"{f.code}  {f.description}"[:44], (STRIP_X + 3, y), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 4.5


def legend_floor(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "FLOOR FINISHES", top)
    used = {r.floor_finish for r in ctx.model.rooms}
    for f in [f for f in ctx.model.finishes if f.category == "floor" and f.code in used]:
        box = Polygon([(STRIP_X + 3, y - 3), (STRIP_X + 13, y - 3), (STRIP_X + 13, y + 3), (STRIP_X + 3, y + 3)])
        _poly(ps, list(box.exterior.coords)[:-1], "G-ANNO-TEXT")
        style, spacing, angle = floor_pattern(f)
        hatch_floor(ps, box, (style, min(max(spacing / ctx.scale, 1.0), 2.5), angle), (STRIP_X + 3, y - 3), "G-ANNO-TEXT")
        _text(ps, f.code, (STRIP_X + 15, y + 1.2), 2.0, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        _text(ps, f.description[:34], (STRIP_X + 15, y - 1.8), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 9


def legend_ga(ctx: Ctx, ps: Paperspace, top: float) -> None:
    y = _legend_box(ps, "AREA SUMMARY", top)
    total = 0.0
    for r in ctx.model.rooms[:12]:
        a = ctx.room_net[r.id].area / 1e6 if r.id in ctx.room_net else 0
        total += a
        _text(ps, r.name[:26], (STRIP_X + 3, y), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        _text(ps, f"{a:.1f} m²", (FRAME[2] - 3, y), 1.8, "G-ANNO-TEXT", ALIGN.MIDDLE_RIGHT)
        y -= 4.2
    if len(ctx.model.rooms) > 12:
        total = sum(n.area for n in ctx.room_net.values()) / 1e6
        _text(ps, "(see schedule for all rooms)", (STRIP_X + 3, y), 1.6, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
        y -= 4.2
    ps.add_line((STRIP_X + 3, y + 2), (FRAME[2] - 3, y + 2), dxfattribs={"layer": "G-ANNO-TEXT"})
    _text(ps, "TOTAL NET AREA", (STRIP_X + 3, y - 1), 1.9, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
    _text(ps, f"{total:.1f} m²", (FRAME[2] - 3, y - 1), 1.9, "G-ANNO-TEXT", ALIGN.MIDDLE_RIGHT)


# ---------------------------------------------------------------- schedules

def _rooms_beside(ctx: Ctx, w: Wall, o: Opening) -> str:
    from shapely.geometry import Point as SP

    names = []
    for side in (1, -1):
        px, py = g.along(w, o.offset + o.width / 2, side * (w.thickness / 2 + 150))
        for r in ctx.model.rooms:
            net = ctx.room_net.get(r.id)
            if net is not None and net.buffer(100).contains(SP(px, py)):
                names.append(r.name)
                break
        else:
            names.append("Outside" if w.kind == "exterior" else "-")
    return " / ".join(names)


def schedule_tables(ctx: Ctx) -> list[tuple[str, list[float], list[str], list[list[str]]]]:
    walls = {w.id: w for w in ctx.model.walls}
    kind_label = {"door": "Single swing door", "double_door": "Double swing door",
                  "sliding_door": "Sliding door", "window": "Window", "opening": "Opening"}
    openings = sorted(ctx.model.openings, key=lambda o: (o.kind == "window", o.tag))
    tables = [
        ("DOOR & WINDOW SCHEDULE", [18, 40, 18, 18, 16, 70],
         ["Tag", "Type", "Width", "Height", "Sill", "Location"],
         [[o.tag, kind_label[o.kind], f"{o.width:.0f}", f"{o.height:.0f}",
           f"{o.sill_height:.0f}" if o.kind == "window" else "-", _rooms_beside(ctx, walls[o.wall_id], o)]
          for o in openings]),
        ("ROOM SCHEDULE", [60, 22, 22, 22, 26],
         ["Room", "Net area m²", "Floor", "Ceiling", "Ceiling ht."],
         [[r.name, f"{ctx.room_net[r.id].area / 1e6:.2f}" if r.id in ctx.room_net else "-",
           r.floor_finish or "-", r.ceiling_finish or "-", f"{r.ceiling_height:.0f}"] for r in ctx.model.rooms]),
        ("FINISHES SCHEDULE", [18, 20, 80, 60, 102],
         ["Code", "Type", "Description", "Material", "Notes"],
         [[f.code, f.category.title(), f.description, f.material, f.notes] for f in ctx.model.finishes]),
        ("FF&E SCHEDULE", [16, 60, 30, 28, 40, 60, 46],
         ["Tag", "Item", "Category", "Size WxD", "Room", "Finish", "Source"],
         [[f.tag, f.name, f.category.replace("_", " "), f"{f.width:.0f}x{f.depth:.0f}",
           next((r.name for r in ctx.model.rooms if r.id == f.room_id), "-"), f.finish, f.source]
          for f in sorted(ctx.model.furniture, key=lambda f: f.tag)]),
    ]
    return [t for t in tables if t[3]]


def _fit(text: str, width_mm: float, h: float) -> str:
    n = max(1, int(width_mm / (h * 0.62)))
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- assembly

def _new_sheet(ctx: Ctx, spec: SheetSpec) -> Paperspace:
    ps = ctx.doc.layouts.new(f"{spec.number} {spec.title}"[:255])
    ps.page_setup(size=(PAPER_W, PAPER_H), margins=(0, 0, 0, 0), units="mm")
    _title_block(ctx, ps, spec)
    return ps


def _schedule_sheets(ctx: Ctx, first_number: int) -> list[str]:
    row_h, head_h, h = 5.0, 6.0, 1.9
    x_left, y_top, y_min = FRAME[0] + 6, FRAME[3] - 8, FRAME[1] + 8
    names = []
    sheet = None
    y = y_top
    number = first_number

    def new():
        nonlocal sheet, y, number
        spec = SheetSpec(f"A-{number}", "Schedules", set())
        sheet = _new_sheet(ctx, spec)
        names.append(sheet.name)
        number += 1
        y = y_top

    for title, widths, headers, rows in schedule_tables(ctx):
        if sheet is None or y - (8 + head_h + 2 * row_h) < y_min:
            new()
        _text(sheet, title, (x_left, y), 3.0, "G-ANNO-TEXT", ALIGN.LEFT)
        y -= 4

        def header():
            nonlocal y
            x = x_left
            _rect(sheet, x_left, y - head_h, x_left + sum(widths), y, "G-ANNO-TEXT", lineweight=25)
            for wd, hd in zip(widths, headers):
                _text(sheet, hd.upper(), (x + 1.5, y - head_h / 2), h, "G-ANNO-TEXT", ALIGN.MIDDLE_LEFT)
                x += wd
            y -= head_h

        header()
        for row in rows:
            if y - row_h < y_min:
                new()
                _text(sheet, f"{title} (CONT.)", (x_left, y), 3.0, "G-ANNO-TEXT", ALIGN.LEFT)
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


BASE = {"A-WALL", "A-WALL-PATT", "G-ROOM-NAME"}
SHEETS = [
    SheetSpec("A-101", "General arrangement plan",
              BASE | {"A-DOOR", "A-GLAZ", "A-DOOR-IDEN", "A-ANNO-DIMS", "A-ANNO-DIMS-INTR", "A-AREA-IDEN"},
              lambda ctx, ps, top: legend_ga(ctx, ps, top)),
    SheetSpec("ID-101", "Furniture layout plan",
              BASE | {"A-DOOR", "A-GLAZ", "I-FURN", "I-FURN-IDEN", "A-ANNO-DIMS"}),
    SheetSpec("ID-102", "Reflected ceiling plan",
              BASE | {"A-DOOR-HEAD", "A-GLAZ", "I-CLNG", "I-CLNG-IDEN"},
              lambda ctx, ps, top: legend_ceiling(ctx, ps, top)),
    SheetSpec("ID-103", "Flooring plan",
              BASE | {"A-DOOR-HEAD", "A-GLAZ", "I-FLOR-PATT", "I-FLOR-IDEN"},
              lambda ctx, ps, top: legend_floor(ctx, ps, top)),
]


def build_dxf(model: BuildingModel) -> tuple[ezdxf.document.Drawing, list[str], int]:
    """Returns the document, the layout names in sheet order and the plan scale."""
    scale = choose_scale(model)
    doc = ezdxf.new("R2018", setup=True)
    _setup(doc, scale)
    geo = g.wall_geometry(model)
    ctx = Ctx(model=model, doc=doc, msp=doc.modelspace(), scale=scale, geo=geo)
    ctx.room_net = {r.id: g.room_net_polygon(r.polygon, geo.solid) for r in model.rooms}

    draw_flooring(ctx)
    draw_walls(ctx)
    draw_openings(ctx)
    draw_room_tags(ctx)
    draw_dimensions(ctx)
    draw_furniture(ctx)
    draw_ceiling(ctx)

    names = []
    for spec in SHEETS:
        if spec.number == "ID-101" and not model.furniture:
            continue
        if spec.number == "ID-102" and not model.ceiling_fixtures:
            continue
        ps = _new_sheet(ctx, spec)
        _viewport(ctx, ps, spec)
        _drawing_title(ctx, ps, spec)
        if spec.legend:
            spec.legend(ctx, ps, FRAME[3] - 30)
        names.append(ps.name)
    names += _schedule_sheets(ctx, 601)
    if "Layout1" in doc.layouts:  # ezdxf's default empty sheet
        doc.layouts.delete("Layout1")
    return doc, names, scale
