"""Read floor-plan images and interior renders with Claude and return a BuildingModel.

The output is a *draft*: it always goes through normalize(), validate() and a
human review step before drawings are issued.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import anthropic
from PIL import Image

from .schema import BuildingModel, json_schema_for_structured_output

MODEL = "claude-opus-5-5"
MAX_IMAGE_EDGE = 2400  # px; large enough to read dimension strings on a scanned plan
MAX_IMAGE_BYTES = 4_500_000

SYSTEM_PROMPT = """\
You are an architectural CAD technician. You convert floor-plan images (scans, \
photos, PDF exports, sketches with written dimensions) and interior renders into \
a precise, structured building model. Draftsmen generate construction drawings \
directly from your output, so accuracy matters more than completeness: when you \
are unsure, say so in extraction_notes rather than inventing detail.

Coordinate system
- Millimetres. Origin at the bottom-left outer corner of the building, +X right, \
+Y up (flip the image's Y axis). Plan north is up unless a north arrow says otherwise; \
if it does, keep the drawing orientation and mention the north direction in extraction_notes.

Establishing scale - do this first
- Prefer written dimension strings. Chains of dimensions must add up to the overall \
dimension; use them to fix every wall position.
- Without dimensions, use a scale bar, a stated scale with a known paper size, or \
standard sizes (internal door leaf ~ 900 mm, WC ~ 700 mm deep, single bed 900x2000, \
kitchen worktop 600 deep). Record which method you used in extraction_notes.

Walls
- One entry per straight wall segment, on the wall CENTRELINE, with real thickness \
(typically exterior 200-300, interior 150-200, partitions 100-125).
- Connected walls must share exact endpoint coordinates (corners) or end exactly \
on the other wall's centreline (T-junctions). Keep walls orthogonal unless clearly angled.
- Do not split a wall at a door or window; openings are separate entries.

Openings
- offset = distance along the wall from its start point to the near edge of the opening.
- hinge says which jamb (wall start or end side) carries the hinges; swing says which \
side of the wall the door opens into, looking from wall start to wall end.
- Tag doors D01, D02... and windows W01, W02... in reading order; plain openings O01...
- Window sill heights: use 900 for standard windows, 0 for full-height glazing, unless shown.

Levels
- One entry in levels per floor shown (GF, FF, SF, RF, B1; or Z1, Z2 for zones of one \
large floor), with ffl in mm when a level is written (e.g. "FFL +4.20" -> 4200). Every \
wall, room, furniture item, fixture and AC unit carries its level id. Each level has its \
own origin at that floor's bottom-left outer corner, aligned so stacked walls share coordinates.

Rooms
- Polygon vertices on wall centrelines, counter-clockwise, not repeating the first point.
- Use the room names and numbers written on the plan; otherwise name rooms by their \
evident use and number them 01, 02... per level in reading order.
- A raised or lowered floor area inside a room (e.g. "EL +0.40" pedicure platform) is a \
separate entry with kind "platform" and floor_level in mm (400). A double-height opening \
in a floor is kind "void". Normal rooms are kind "room".

Status (prices depend on it)
- status is "new", "reuse", "modify", "relocate" or "remove" only when the drawing or \
brief says so (e.g. "TO BE DISMANTLED" -> remove, "TO BE MODIFIED" -> modify, "existing \
to remain" -> reuse). Otherwise leave it empty so the designer must decide.

AC units
- Add ac_units only where split units/indoor units are shown or the brief asks for them: \
tag AC-01..., room, type, position. Leave capacity and outdoor_location empty unless written.

Interior content
- Furniture and fixtures drawn on the plan: add with source "floor plan".
- Renders and mood images: identify the furniture, lighting and finishes they show and \
place appropriate items in the room they most likely depict, at realistic sizes, \
against walls where appropriate, leaving circulation of at least 800 mm and not \
blocking door swings. Set source to "render N" (N = the image number).
- Furniture rotation: an item's front faces its local -Y; rotation 0 means the back is \
against a wall to the north (+Y), 90 = back against the east wall, 180 = south, 270 = west.
- Propose a ceiling layout for every room: downlights at roughly 1200-1800 mm spacing \
kept 500 mm+ from walls, pendants over dining/island, an exhaust fan in wet rooms, \
a smoke detector in bedrooms and corridors, AC supply diffusers where sensible.
- Finishes: give codes FL-xx (floor), CL-xx (ceiling), WL-xx (wall), SK-xx (skirting), JN-xx (joinery). \
Take materials from the renders when shown (e.g. "Porcelain tile 600x600 matt"); \
otherwise propose sensible defaults and say so in extraction_notes. Every room needs \
floor_finish, wall_finish and ceiling_finish codes that exist in finishes.

Project info: fill what is visible on the drawing's title block (project name, code, client, \
address, stage, revision); leave the rest empty. Use stage "CD" and revision "A" if none is shown.
"""


@dataclass
class InputImage:
    path: Path
    role: str  # "plan" | "render"


def _image_block(path: Path) -> dict:
    mime = mimetypes.guess_type(path.name)[0] or ""
    data = path.read_bytes()
    if mime == "application/pdf":
        return {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(data).decode()}}
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    media = "image/png"
    if buf.tell() > MAX_IMAGE_BYTES:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        media = "image/jpeg"
    return {"type": "image",
            "source": {"type": "base64", "media_type": media,
                       "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def build_messages(images: list[InputImage], brief: str) -> list[dict]:
    content: list[dict] = []
    plans = [i for i in images if i.role == "plan"]
    renders = [i for i in images if i.role == "render"]
    for n, img in enumerate(plans, 1):
        content.append({"type": "text", "text": f"Floor plan {n}: {img.path.name}"})
        content.append(_image_block(img.path))
    for n, img in enumerate(renders, 1):
        content.append({"type": "text", "text": f"Render {n}: {img.path.name}"})
        content.append(_image_block(img.path))
    task = ("Extract the complete building model from the floor plan(s) above"
            + (" and furnish it from the renders." if renders else ".")
            + " Follow the conventions in the system prompt exactly.")
    if brief.strip():
        task += f"\n\nDesigner's brief and known facts (treat these as correct):\n{brief.strip()}"
    content.append({"type": "text", "text": task})
    return [{"role": "user", "content": content}]


class ExtractionError(RuntimeError):
    pass


def extract(images: list[InputImage], brief: str = "", client: anthropic.Anthropic | None = None) -> BuildingModel:
    if not any(i.role == "plan" for i in images):
        raise ExtractionError("At least one floor plan image is required.")
    client = client or anthropic.Anthropic()
    schema = json_schema_for_structured_output(BuildingModel)
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM_PROMPT,
            messages=build_messages(images, brief),
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
    except anthropic.BadRequestError as e:
        raise ExtractionError(f"The request was rejected: {e.message}") from e
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Anthropic API key is missing or invalid (set ANTHROPIC_API_KEY).") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Rate limited by the Anthropic API - try again in a minute.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Could not reach the Anthropic API.") from e

    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to process these images.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The plan is too large to extract in one pass - split it into parts.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return BuildingModel.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValueError) as e:
        raise ExtractionError(f"Model output did not match the schema: {e}") from e
