"""Geometry clean-up and derived shapes (wall poché, net room areas, openings)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from .schema import BuildingModel, Opening, Point, Wall

GRID = 5.0  # mm; extracted coordinates are rounded to this
ALIGN_TOL = 30.0  # mm; coordinates closer than this are treated as the same line
ORTHO_TOL_DEG = 3.0


# ---------------------------------------------------------------- vectors

def vec(a: Point, b: Point) -> tuple[float, float]:
    return b.x - a.x, b.y - a.y


def length(w: Wall) -> float:
    dx, dy = vec(w.start, w.end)
    return math.hypot(dx, dy)


def unit(w: Wall) -> tuple[float, float]:
    dx, dy = vec(w.start, w.end)
    ln = math.hypot(dx, dy) or 1.0
    return dx / ln, dy / ln


def left_normal(w: Wall) -> tuple[float, float]:
    ux, uy = unit(w)
    return -uy, ux


def along(w: Wall, dist: float, side: float = 0.0) -> tuple[float, float]:
    """Point `dist` mm along the wall from its start, shifted `side` mm to its left."""
    ux, uy = unit(w)
    nx, ny = -uy, ux
    return w.start.x + ux * dist + nx * side, w.start.y + uy * dist + ny * side


# ---------------------------------------------------------------- clean-up

def _cluster_1d(values: list[float], tol: float) -> dict[float, float]:
    """Map each value to the mean of its cluster (values chained within tol)."""
    if not values:
        return {}
    ordered = sorted(set(values))
    clusters: list[list[float]] = [[ordered[0]]]
    for v in ordered[1:]:
        if v - clusters[-1][-1] <= tol:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    mapping = {}
    for c in clusters:
        mean = round(sum(c) / len(c) / GRID) * GRID
        for v in c:
            mapping[v] = mean
    return mapping


def _straighten(w: Wall) -> None:
    dx, dy = vec(w.start, w.end)
    ang = abs(math.degrees(math.atan2(dy, dx))) % 180
    if min(ang, 180 - ang) <= ORTHO_TOL_DEG:  # horizontal
        y = (w.start.y + w.end.y) / 2
        w.start.y = w.end.y = y
    elif abs(ang - 90) <= ORTHO_TOL_DEG:  # vertical
        x = (w.start.x + w.end.x) / 2
        w.start.x = w.end.x = x


def _project_onto(p: Point, w: Wall) -> tuple[float, Point]:
    ux, uy = unit(w)
    t = (p.x - w.start.x) * ux + (p.y - w.start.y) * uy
    t = max(0.0, min(length(w), t))
    q = Point(x=w.start.x + ux * t, y=w.start.y + uy * t)
    return math.hypot(p.x - q.x, p.y - q.y), q


def normalize(model: BuildingModel) -> BuildingModel:
    """Snap the AI's geometry so walls meet cleanly. Returns a new model."""
    m = model.model_copy(deep=True)
    for w in m.walls:
        _straighten(w)

    # Align coordinates across all walls and rooms so corners actually meet.
    pts = [p for w in m.walls for p in (w.start, w.end)]
    pts += [p for r in m.rooms for p in r.polygon]
    xmap = _cluster_1d([p.x for p in pts], ALIGN_TOL)
    ymap = _cluster_1d([p.y for p in pts], ALIGN_TOL)
    for p in pts:
        p.x, p.y = xmap[p.x], ymap[p.y]

    # T-junctions: a free wall end close to another wall is pulled onto it.
    for w in m.walls:
        for attr in ("start", "end"):
            p = getattr(w, attr)
            for host in m.walls:
                if host is w:
                    continue
                d, q = _project_onto(p, host)
                if 0 < d <= max(host.thickness / 2 + 10, ALIGN_TOL):
                    setattr(w, attr, Point(x=round(q.x / GRID) * GRID, y=round(q.y / GRID) * GRID))
                    break

    m.walls = [w for w in m.walls if length(w) >= 50]
    wall_len = {w.id: length(w) for w in m.walls}
    kept = []
    for o in m.openings:
        if o.wall_id not in wall_len:
            continue
        o.width = max(100.0, min(o.width, wall_len[o.wall_id]))
        o.offset = max(0.0, min(o.offset, wall_len[o.wall_id] - o.width))
        kept.append(o)
    m.openings = kept
    return m


# ---------------------------------------------------------------- shapes

def _end_connected(p: Point, w: Wall, walls: list[Wall]) -> bool:
    for other in walls:
        if other is w:
            continue
        d, _ = _project_onto(p, other)
        if d <= other.thickness / 2 + 1:
            return True
    return False


def wall_band(w: Wall, walls: list[Wall]) -> Polygon:
    """Wall rectangle; connected ends are extended so corners fill in."""
    ux, uy = unit(w)
    ext = w.thickness / 2
    s0 = ext if _end_connected(w.start, w, walls) else 0.0
    e0 = ext if _end_connected(w.end, w, walls) else 0.0
    line = LineString([
        (w.start.x - ux * s0, w.start.y - uy * s0),
        (w.end.x + ux * e0, w.end.y + uy * e0),
    ])
    return line.buffer(w.thickness / 2, cap_style="flat", join_style="mitre")


def opening_cut(o: Opening, w: Wall) -> Polygon:
    t = w.thickness / 2 + 5
    pts = [along(w, o.offset, -t), along(w, o.offset + o.width, -t),
           along(w, o.offset + o.width, t), along(w, o.offset, t)]
    return Polygon(pts)


@dataclass
class WallGeometry:
    solid: Polygon | MultiPolygon  # all walls, no openings (for room areas)
    by_kind: dict[str, Polygon | MultiPolygon]  # with openings cut, for poché
    outline: Polygon | MultiPolygon  # union with openings cut


def wall_geometry(model: BuildingModel) -> WallGeometry:
    walls = model.walls
    by_id = {w.id: w for w in walls}
    bands = {w.id: wall_band(w, walls) for w in walls}
    solid = unary_union(list(bands.values())) if bands else Polygon()
    cuts = unary_union([opening_cut(o, by_id[o.wall_id]) for o in model.openings if o.wall_id in by_id])
    outline = solid.difference(cuts) if not cuts.is_empty else solid
    by_kind: dict[str, Polygon | MultiPolygon] = {}
    claimed = Polygon()
    for kind in ("exterior", "interior", "partition"):
        geom = unary_union([bands[w.id] for w in walls if w.kind == kind])
        if geom.is_empty:
            continue
        geom = geom.difference(claimed).intersection(outline)
        claimed = claimed.union(geom)
        if not geom.is_empty:
            by_kind[kind] = geom
    return WallGeometry(solid=solid, by_kind=by_kind, outline=outline)


def polygons(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def room_net_polygon(room_pts: list[Point], walls_solid) -> Polygon:
    if len(room_pts) < 3:
        return Polygon()
    poly = Polygon([(p.x, p.y) for p in room_pts]).buffer(0)
    net = poly.difference(walls_solid)
    parts = polygons(net)
    return max(parts, key=lambda g: g.area) if parts else Polygon()


def extents(model: BuildingModel) -> tuple[float, float, float, float]:
    pts = [(p.x, p.y) for w in model.walls for p in (w.start, w.end)]
    pts += [(p.x, p.y) for r in model.rooms for p in r.polygon]
    if not pts:
        return 0.0, 0.0, 10000.0, 10000.0
    pad = max((w.thickness for w in model.walls), default=0) / 2
    xs, ys = zip(*pts)
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
