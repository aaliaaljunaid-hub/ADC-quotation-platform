"""Building model: the single source of truth every drawing is generated from.

All dimensions are millimetres. The origin is the bottom-left corner of the
plan, +X to the right and +Y up (CAD convention, not image convention).
Walls are stored as centrelines with a thickness. Every element belongs to a
level (GF, FF, ...); each level gets its own set of plan sheets.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(_Strict):
    x: float
    y: float


# Issue stages from the ADC drawing register, in project order.
Stage = Literal["CD", "SD", "DD", "AP", "IFT", "IFC", "AB"]
STAGE_NAMES = {
    "CD": "Concept Design",
    "SD": "Schematic Design",
    "DD": "Design Development",
    "AP": "Authority / Permit",
    "IFT": "Issued for Tender",
    "IFC": "Issued for Construction",
    "AB": "As-Built",
}

# New / Reuse / Modify decides the price of every item, so it is tracked everywhere.
ItemStatus = Literal["", "new", "reuse", "modify", "relocate", "remove"]


class Revision(_Strict):
    rev: str = Field(description="Letters (A, B, C) before IFC, numbers (0, 1, 2) from IFC")
    date: str = ""
    description: str = ""
    by: str = ""


class ProjectInfo(_Strict):
    project_name: str = "Untitled project"
    project_code: str = Field("", description="Short code used in sheet numbers, e.g. VLA24")
    client: str = ""
    location: str = Field("", description="Full site address")
    company_name: str = "Aalia Designs & Consultations"
    drawn_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    date: str = ""
    stage: Stage = "CD"
    revision: str = "A"
    revisions: list[Revision] = Field(default_factory=list)


class Level(_Strict):
    id: str = Field(description="Short suffix used in sheet numbers: GF, FF, SF, RF, B1, Z1 ...")
    name: str = Field(description="e.g. Ground Floor")
    ffl: float = Field(0, description="Finished floor level in mm relative to project datum")


WallKind = Literal["exterior", "interior", "partition"]


class Wall(_Strict):
    id: str
    level: str = "GF"
    start: Point
    end: Point
    thickness: float = Field(description="Wall thickness in mm, e.g. 200 exterior block, 100 partition")
    kind: WallKind = "interior"
    height: float = 3000
    status: ItemStatus = ""


OpeningKind = Literal["door", "double_door", "sliding_door", "window", "opening"]


class Opening(_Strict):
    id: str
    tag: str = Field(description="Schedule tag, e.g. D01 or W01")
    wall_id: str
    kind: OpeningKind
    offset: float = Field(description="Distance in mm along the wall from wall.start to the near edge of the opening")
    width: float
    height: float = 2100
    sill_height: float = 0
    hinge: Literal["start", "end"] = Field(
        "start", description="Which jamb the door is hinged on, relative to the wall direction start->end"
    )
    swing: Literal["left", "right"] = Field(
        "left", description="Side of the wall the door swings into, looking from wall.start towards wall.end"
    )
    status: ItemStatus = ""


class Room(_Strict):
    id: str
    level: str = "GF"
    number: str = Field("", description="Room number, e.g. 01")
    name: str
    kind: Literal["room", "platform", "void"] = Field(
        "room", description="platform = raised/lowered floor area inside a room; void = double-height opening in the floor"
    )
    polygon: list[Point] = Field(description="Room boundary on wall centrelines, counter-clockwise, not closed")
    floor_level: float = Field(0, description="mm above the level's FFL, e.g. 400 for a raised platform")
    floor_finish: str = Field("", description="Finish code from the finishes list, e.g. FL-01")
    skirting: str = Field("", description="Finish code of the skirting, e.g. SK-01")
    wall_finish: str = Field("", description="Main wall finish code, e.g. WL-01 (feature walls in notes)")
    ceiling_finish: str = Field("", description="Finish code, e.g. CL-01")
    ceiling_height: float = 2700


FurnitureCategory = Literal[
    "sofa", "armchair", "coffee_table", "dining_table", "chair", "bed", "bedside_table",
    "wardrobe", "desk", "office_chair", "tv_unit", "sideboard", "bookshelf", "rug",
    "kitchen_counter", "kitchen_island", "fridge", "cooker", "sink", "wc", "basin",
    "shower", "bathtub", "reception_desk", "meeting_table", "plant", "other",
]


class Furniture(_Strict):
    id: str
    tag: str = Field(description="FF&E tag, e.g. F01")
    level: str = "GF"
    name: str
    category: FurnitureCategory = "other"
    center: Point
    width: float = Field(description="Size along the item's local X axis (mm)")
    depth: float = Field(description="Size along the item's local Y axis (mm); the 'front' faces local -Y")
    rotation: float = Field(0, description="Degrees counter-clockwise")
    room_id: str = ""
    finish: str = ""
    status: ItemStatus = ""
    source: str = Field("", description="Where the item came from, e.g. 'floor plan' or 'render 2'")


FixtureKind = Literal[
    "downlight", "pendant", "linear_light", "spotlight", "wall_light", "ac_supply",
    "ac_return", "smoke_detector", "speaker", "exhaust_fan", "access_panel",
]


class CeilingFixture(_Strict):
    id: str
    level: str = "GF"
    kind: FixtureKind
    position: Point
    length: float = Field(0, description="Only for linear_light: length in mm")
    rotation: float = 0
    room_id: str = ""
    status: ItemStatus = ""


ACType = Literal["wall_mounted", "ceiling_cassette", "concealed_ducted", "floor_standing"]


class ACUnit(_Strict):
    id: str
    tag: str = Field(description="AC-01, AC-02 ...")
    level: str = "GF"
    room_id: str = ""
    type: ACType = "wall_mounted"
    position: Point
    rotation: float = Field(0, description="Wall units: the air outlet faces local -Y")
    capacity: str = Field("", description="e.g. '18,000 BTU/h' or 'by MEP'")
    mounting_height: float = Field(0, description="mm AFFL to underside; 0 for ceiling units")
    outdoor_location: str = ""
    status: ItemStatus = "new"
    notes: str = ""


class Finish(_Strict):
    code: str = Field(description="e.g. FL-01, CL-01")
    category: Literal["floor", "ceiling", "wall", "skirting", "joinery"]
    description: str
    material: str = ""
    notes: str = ""


class BuildingModel(_Strict):
    units: Literal["mm"] = "mm"
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    levels: list[Level] = Field(default_factory=list)
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    furniture: list[Furniture] = Field(default_factory=list)
    ceiling_fixtures: list[CeilingFixture] = Field(default_factory=list)
    ac_units: list[ACUnit] = Field(default_factory=list)
    finishes: list[Finish] = Field(default_factory=list)
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Assumptions and uncertainties a reviewer must check (unreadable dimensions, guessed scale, ...)",
    )

    def level_ids(self) -> list[str]:
        """Levels in drawing order; elements on undeclared levels still get sheets."""
        ids = [lv.id for lv in self.levels]
        for coll in (self.walls, self.rooms, self.furniture, self.ceiling_fixtures, self.ac_units):
            for item in coll:
                if item.level not in ids:
                    ids.append(item.level)
        return ids or ["GF"]

    def level(self, level_id: str) -> Level:
        default_names = {"GF": "Ground Floor", "FF": "First Floor", "SF": "Second Floor", "RF": "Roof", "B1": "Basement"}
        return next((lv for lv in self.levels if lv.id == level_id),
                    Level(id=level_id, name=default_names.get(level_id, level_id), ffl=0))


def json_schema_for_structured_output(model: type[BaseModel]) -> dict:
    """Pydantic schema trimmed to what the structured-output endpoint accepts."""
    drop = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}

    def clean(node, field_map=False):
        if isinstance(node, dict):
            if field_map:  # keys are field names, not schema keywords
                return {k: clean(v) for k, v in node.items()}
            out = {
                k: clean(v, field_map=k in ("properties", "$defs"))
                for k, v in node.items()
                if k not in drop
            }
            if out.get("type") == "object":
                out["additionalProperties"] = False
                # Every property required keeps the model from silently omitting fields.
                if "properties" in out:
                    out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(model.model_json_schema())
