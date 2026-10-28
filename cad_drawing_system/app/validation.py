"""Checks run before drawings are issued.

- error:   blocks generation (the drawings would be wrong)
- warning: for the reviewer's judgement
- pricing: information a quantity surveyor needs to price without asking
  (the questions from ADC's tender-drawing checklist, raised automatically)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from shapely.affinity import rotate
from shapely.geometry import Point as SPoint
from shapely.geometry import Polygon

from . import geometry as g
from .schema import BuildingModel


@dataclass
class Issue:
    level: str  # "error" | "warning" | "pricing"
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

    ids = [x.id for x in [*model.walls, *model.openings, *model.rooms, *model.furniture, *model.ac_units]]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        add("error", dup, "Duplicate id.")
    for coll, label in ((model.openings, "Opening"), (model.furniture, "FF&E"), (model.ac_units, "AC")):
        tags = [x.tag for x in coll if x.tag]
        for dup in sorted({t for t in tags if tags.count(t) > 1}):
            add("warning", dup, f"{label} tag used more than once.")
    numbers = [(r.level, r.number) for r in model.rooms if r.number and r.kind == "room"]
    for lv, num in sorted({n for n in numbers if numbers.count(n) > 1}):
        add("warning", f"{lv} {num}", "Room number used more than once on this level.")

    walls = {w.id: w for w in model.walls}
    for o in model.openings:
        if o.wall_id not in walls:
            add("error", o.tag or o.id, f"Opening refers to missing wall '{o.wall_id}'.")

    for lv in model.level_ids():
        _check_level(g.level_model(model, lv), lv, add, multi=len(model.level_ids()) > 1)

    _check_project(model, add)
    _check_pricing(model, add)
    return issues


def _check_level(model: BuildingModel, lv: str, add, multi: bool) -> None:
    where = f"{lv} " if multi else ""
    x0, y0, x1, y1 = g.extents(model)
    if model.walls and max(x1 - x0, y1 - y0) < 2000:
        add("warning", f"{where}scale", "Plan is smaller than 2 m overall - the scale was probably misread.")
    if max(x1 - x0, y1 - y0) > 200000:
        add("warning", f"{where}scale", "Plan is larger than 200 m overall - the scale was probably misread.")

    walls = {w.id: w for w in model.walls}
    for w in model.walls:
        if not 50 <= w.thickness <= 600:
            add("warning", w.id, f"Unusual wall thickness {w.thickness:.0f} mm.")

    by_wall: dict[str, list] = {}
    for o in model.openings:
        w = walls[o.wall_id]
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

    geo = g.wall_geometry(model)
    cuts = [g.opening_cut(o, walls[o.wall_id]) for o in model.openings]
    room_polys = {}
    for r in model.rooms:
        label = f"{r.number} {r.name}".strip()
        poly = Polygon([(p.x, p.y) for p in r.polygon]) if len(r.polygon) >= 3 else Polygon()
        if poly.is_empty or not poly.is_valid:
            add("error", label, "Room boundary is not a valid closed shape.")
            continue
        room_polys[r.id] = poly
        if r.kind != "room":
            continue
        net = g.room_net_polygon(r.polygon, geo.solid)
        if net.area < 1.0e6:
            add("warning", label, f"Net area only {net.area / 1e6:.2f} m2.")
        if not any(poly.exterior.buffer(400).intersects(c) for c in cuts):
            add("warning", label, "Room has no door or opening on its boundary.")
        if not 2100 <= r.ceiling_height <= 6000:
            add("warning", label, f"Ceiling height {r.ceiling_height:.0f} mm is unusual.")

    for f in model.furniture:
        fp = furniture_footprint(f)
        if fp.intersection(geo.solid).area > 0.05 * fp.area:
            add("warning", f.tag or f.name, f"'{f.name}' overlaps a wall.")
        if f.room_id in room_polys and not room_polys[f.room_id].buffer(50).contains(SPoint(f.center.x, f.center.y)):
            add("warning", f.tag or f.name, f"'{f.name}' is outside its room.")

    for c in model.ceiling_fixtures:
        if c.room_id in room_polys and not room_polys[c.room_id].contains(SPoint(c.position.x, c.position.y)):
            add("warning", c.id, f"{c.kind} is outside its room.")
    for a in model.ac_units:
        if a.room_id in room_polys and not room_polys[a.room_id].buffer(300).contains(SPoint(a.position.x, a.position.y)):
            add("warning", a.tag, "AC unit is outside its room.")


def _check_project(model: BuildingModel, add) -> None:
    p = model.project
    if not re.fullmatch(r"[A-Z0-9]{2,10}", p.project_code or ""):
        add("warning", "Project code", "Set a project code (2-10 capitals/digits, e.g. VLA24) - it is part of every sheet number.")
    numeric = p.stage in ("IFC", "AB")
    if numeric and not p.revision.isdigit():
        add("warning", "Revision", f"Stage {p.stage}: revisions are numbers (0, 1, 2...) once issued for construction.")
    if not numeric and not re.fullmatch(r"[A-Z]{1,2}", p.revision or ""):
        add("warning", "Revision", f"Stage {p.stage}: revisions are letters (A, B, C...) before IFC.")
    if p.revisions and p.revisions[-1].rev != p.revision:
        add("warning", "Revision", f"Current revision {p.revision} is not the latest entry in the revision history.")
    if p.stage in ("IFT", "IFC", "AB") and not p.checked_by:
        add("warning", "Checked by", f"Sheets issued at {p.stage} need checked-by initials.")
    if p.stage in ("IFC", "AB") and not p.approved_by:
        add("warning", "Approved by", f"Sheets issued at {p.stage} need approved-by initials.")


def _check_pricing(model: BuildingModel, add) -> None:
    add_p = lambda item, msg: add("pricing", item, msg)  # noqa: E731
    p = model.project
    for field, label in (("client", "Client"), ("location", "Site address")):
        if not getattr(p, field).strip():
            add_p(label, f"{label} is missing from the title block.")

    finishes = {f.code: f for f in model.finishes}
    used = set()
    for r in model.rooms:
        label = f"{r.number} {r.name}".strip()
        if r.kind == "void":
            continue
        if r.kind == "room" and not r.number:
            add_p(r.name, "Room has no number.")
        for code, what in ((r.floor_finish, "floor"), (r.wall_finish, "wall"), (r.ceiling_finish, "ceiling")):
            if r.kind == "platform" and what != "floor":
                continue
            if not code:
                add_p(label, f"No {what} finish code.")
            elif code not in finishes:
                add_p(label, f"Finish code {code} is not in the finishes schedule.")
            used.add(code)
        used.add(r.skirting)
        pts = [(q.x, q.y) for q in r.polygon]
        if len(pts) >= 3:
            poly = Polygon(pts)
            bx0, by0, bx1, by1 = poly.bounds
            if r.kind == "room" and poly.area < 0.97 * (bx1 - bx0) * (by1 - by0):
                add_p(label, "Room is not rectangular - add setting-out dimensions on ID-203 by hand.")
    for f in model.finishes:
        if not f.material.strip():
            add_p(f.code, "Finish has no material/specification.")
        if f.code not in used and f.category in ("floor", "ceiling", "wall", "skirting"):
            add("warning", f.code, "Finish is scheduled but not used in any room.")

    for f in model.furniture:
        if not f.status:
            add_p(f.tag or f.name, f"'{f.name}': mark New / Reuse / Modify / Relocate / Remove.")
        elif f.status in ("new", "modify") and not f.finish.strip():
            add_p(f.tag or f.name, f"'{f.name}': no material or finish to price from.")
    for o in model.openings:
        if o.kind != "window" and not o.status:
            add_p(o.tag, "Door/opening: mark New / Reuse / Modify / Remove.")
    for c in model.ceiling_fixtures:
        if not c.status:
            add_p(c.id, f"{c.kind.replace('_', ' ')}: mark New / Reuse / Relocate / Remove.")
            break  # one reminder is enough for fixtures
    for a in model.ac_units:
        missing = [label for value, label in ((a.capacity, "capacity"), (a.outdoor_location, "outdoor unit location"))
                   if not value.strip()]
        if a.type == "wall_mounted" and not a.mounting_height:
            missing.append("mounting height")
        if missing:
            add_p(a.tag, "AC unit: no " + ", ".join(missing) + " (write 'by MEP' if the engineer will decide).")


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
