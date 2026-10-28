"""Building model: the single source of truth every drawing is generated from.

All dimensions are millimetres. The origin is the bottom-left corner of the
plan, +X to the right and +Y up (CAD convention, not image convention).
Walls are stored as centrelines with a thickness.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Point(_Strict):
    x: float
    y: float


class ProjectInfo(_Strict):
    project_name: str = "Untitled project"
    client: str = ""
    location: str = ""
    company_name: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    date: str = ""
    revision: str = "P01"
    status: str = "PRELIMINARY - NOT FOR CONSTRUCTION"
    project_number: str = ""


WallKind = Literal["exterior", "interior", "partition"]


class Wall(_Strict):
    id: str
    start: Point
    end: Point
    thickness: float = Field(description="Wall thickness in mm, e.g. 200 exterior block, 100 partition")
    kind: WallKind = "interior"
    height: float = 3000


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


class Room(_Strict):
    id: str
    name: str
    polygon: list[Point] = Field(description="Room boundary on wall centrelines, counter-clockwise, not closed")
    floor_finish: str = Field("", description="Finish code from the finishes list, e.g. FL-01")
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
    name: str
    category: FurnitureCategory = "other"
    center: Point
    width: float = Field(description="Size along the item's local X axis (mm)")
    depth: float = Field(description="Size along the item's local Y axis (mm); the 'front' faces local -Y")
    rotation: float = Field(0, description="Degrees counter-clockwise")
    room_id: str = ""
    finish: str = ""
    source: str = Field("", description="Where the item came from, e.g. 'floor plan' or 'render 2'")


FixtureKind = Literal[
    "downlight", "pendant", "linear_light", "spotlight", "wall_light", "ac_supply",
    "ac_return", "smoke_detector", "speaker", "exhaust_fan", "access_panel",
]


class CeilingFixture(_Strict):
    id: str
    kind: FixtureKind
    position: Point
    length: float = Field(0, description="Only for linear_light: length in mm")
    rotation: float = 0
    room_id: str = ""


class Finish(_Strict):
    code: str = Field(description="e.g. FL-01, CL-01")
    category: Literal["floor", "ceiling", "wall", "joinery"]
    description: str
    material: str = ""
    notes: str = ""


class BuildingModel(_Strict):
    units: Literal["mm"] = "mm"
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    furniture: list[Furniture] = Field(default_factory=list)
    ceiling_fixtures: list[CeilingFixture] = Field(default_factory=list)
    finishes: list[Finish] = Field(default_factory=list)
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Assumptions and uncertainties a reviewer must check (unreadable dimensions, guessed scale, ...)",
    )


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
