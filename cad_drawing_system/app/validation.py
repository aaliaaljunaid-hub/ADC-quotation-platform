"""Checks run before drawings are issued. Errors block generation; warnings
are printed for the reviewer."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon
from shapely.affinity import rotate

from . import geometry as g
from .schema import BuildingModel


@dataclass
class Issue:
    level: str  # "error" | "warning"
    item: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


def furniture_footprint(f) -> Polygon:
    hw, hd = f.width / 2, f.depth / 2
    box = Polygon([(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)])
    box = rotate(box, f.rotation, origin=(0, 0))
    return Polygon([(x + f.center.x, y + f.center.y) for x, y in box.exterior.coords])


def validate(model: BuildingModel) -> list[Issue]:
    issues: list[Issue] = []
    add = lambda level, item, msg: issues.append(Issue(level, item, msg))  # noqa: E731

    if not model.walls:
        add("error", "walls", "The model has no walls.")
        return issues

    ids = [x.id for x in [*model.walls, *model.openings, *model.rooms, *model.furniture]]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        add("error", dup, "Duplicate id.")

    x0, y0, x1, y1 = g.extents(model)
    if max(x1 - x0, y1 - y0) < 2000:
        add("warning", "scale", "Plan is smaller than 2 m overall - the scale was probably misread.")
    if max(x1 - x0, y1 - y0) > 200000:
        add("warning", "scale", "Plan is larger than 200 m overall - the scale was probably misread.")

    walls = {w.id: w for w in model.walls}
    for w in model.walls:
        if not 50 <= w.thickness <= 600:
            add("warning", w.id, f"Unusual wall thickness {w.thickness:.0f} mm.")

    by_wall: dict[str, list] = {}
    for o in model.openings:
        w = walls.get(o.wall_id)
        if w is None:
            add("error", o.tag or o.id, f"Opening refers to missing wall '{o.wall_id}'.")
            continue
        if o.offset < 0 or o.offset + o.width > g.length(w) + 1:
            add("error", o.tag or o.id, "Opening runs past the end of its wall.")
        if o.kind in ("door", "double_door", "sliding_door") and not 600 <= o.width <= 3000:
            add("warning", o.tag or o.id, f"Door width {o.width:.0f} mm is unusual.")
        if o.kind == "door" and o.width < 800:
            add("warning", o.tag or o.id, "Door clear width under 800 mm - check accessibility requirements.")
        by_wall.setdefault(o.wall_id, []).append(o)
    for wid, ops in by_wall.items():
        ops = sorted(ops, key=lambda o: o.offset)
        for a, b in zip(ops, ops[1:]):
            if a.offset + a.width > b.offset:
                add("error", f"{a.tag}/{b.tag}", f"Openings overlap on wall {wid}.")

    tags = [o.tag for o in model.openings if o.tag]
    for dup in sorted({t for t in tags if tags.count(t) > 1}):
        add("warning", dup, "Opening tag used more than once.")

    geo = g.wall_geometry(model)
    cuts = [g.opening_cut(o, walls[o.wall_id]) for o in model.openings if o.wall_id in walls]
    room_polys = {}
    for r in model.rooms:
        poly = Polygon([(p.x, p.y) for p in r.polygon]) if len(r.polygon) >= 3 else Polygon()
        if poly.is_empty or not poly.is_valid:
            add("error", r.name, "Room boundary is not a valid closed shape.")
            continue
        room_polys[r.id] = poly
        net = g.room_net_polygon(r.polygon, geo.solid)
        if net.area < 1.0e6:
            add("warning", r.name, f"Net area only {net.area / 1e6:.2f} m2.")
        if not any(poly.exterior.buffer(400).intersects(c) for c in cuts):
            add("warning", r.name, "Room has no door or opening on its boundary.")
        if not 2100 <= r.ceiling_height <= 6000:
            add("warning", r.name, f"Ceiling height {r.ceiling_height:.0f} mm is unusual.")

    finish_codes = {f.code for f in model.finishes}
    for r in model.rooms:
        for code in (r.floor_finish, r.ceiling_finish):
            if code and code not in finish_codes:
                add("warning", r.name, f"Finish code {code} is not in the finishes list.")

    for f in model.furniture:
        fp = furniture_footprint(f)
        if fp.intersection(geo.solid).area > 0.05 * fp.area:
            add("warning", f.tag or f.name, f"'{f.name}' overlaps a wall.")
        if f.room_id and f.room_id in room_polys and not room_polys[f.room_id].buffer(50).contains(SPoint(f.center.x, f.center.y)):
            add("warning", f.tag or f.name, f"'{f.name}' is outside its room.")

    for c in model.ceiling_fixtures:
        if c.room_id in room_polys and not room_polys[c.room_id].contains(SPoint(c.position.x, c.position.y)):
            add("warning", c.id, f"{c.kind} is outside its room.")

    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
