# ADC CAD Drawing System

Turns floor-plan images and interior renders into an editable **DXF drawing set**
(opens in AutoCAD, BricsCAD, DraftSight, LibreCAD) and an issued **PDF set**.

```
images ──► AI extraction ──► building model ──► human review ──► validation ──► DXF + PDF
          (Claude vision)    (walls, doors,     (web UI tables,   (blocks on
                              rooms, FF&E,       live preview)     errors)
                              ceiling, finishes)
```

The building model (`app/schema.py`) is the single source of truth. Every
sheet is generated from it, so a correction made once appears on every
drawing and schedule.

## Drawing set produced (ADC Interior Design Drawing Register)

Sheet numbers, titles and order follow the ADC drawing register. The full
number is `ADC-[Project code]-ID-[Series][Sheet]-[Revision]`, e.g.
`ADC-VLA24-ID-203-B`. Multi-floor projects get one plan sheet per level with a
suffix (`ID-201.GF`, `ID-201.FF`); schedules cover the whole project.

| Sheet | Content | Issued at |
|---|---|---|
| ID-201 General Arrangement Plan | Walls with poché, doors, windows, room numbers, names and net areas, platforms with levels, voids, exterior dimension chains, area summary | SD onwards |
| ID-202 Furniture Layout Plan | FF&E symbols with tags; tag suffix shows status (-R reuse, -M modify, -RL relocate, -X remove) | all stages (only sheet at CD) |
| ID-203 Dimensioned Layout Plan | Exterior chains plus clear room dimensions, furniture for setting out | SD onwards |
| ID-204 Flooring Finish Plan | True-size patterns (tile grid from "600x600", planks, carpet), finish codes, platforms with their own finish, legend | SD onwards |
| ID-208 Door & Opening Plan | Door and window tags linked to ID-906 | SD onwards |
| ID-301 Reflected Ceiling Plan (RCP) | Fixtures, ceiling finish and height per room, legend | SD onwards |
| ID-405 HVAC Coordination Plan | Split units drawn to scale with tags and set-out dimensions from the nearest walls; marked "for coordination only" | DD onwards |
| ID-901 Room Finishes Schedule | No., room, area, floor, skirting, walls, ceiling, CH, remarks | SD, DD, IFT, IFC, AB |
| ID-902 Material & Finishes Schedule | Code, type, description, material, rooms where used, supplier/notes | 〃 |
| ID-905 Ceiling Schedule | Code, type, finish, heights, area, rooms | 〃 |
| ID-906 Door Schedule | Doors and windows: tag, type, size, status, location (rooms each side) | 〃 |
| ID-913 FF&E Schedule — Furniture | Identical items grouped with quantity, size, finish, status, location, source | 〃 |
| ID-917 HVAC Equipment Schedule | AC-xx tag, room, area, indoor type, capacity, mounting height, outdoor unit location | 〃 |

The project's **issue stage** (CD, SD, DD, AP, IFT, IFC, AB) selects the sheets
using the register's "which series to issue at each stage" table. At CD only
ID-202 is generated, as in the concept client deck. Schedules are not issued
at AP.

All sheets are A3 landscape with the register's title block fields: AALIA logo,
project name and code, client, site address, sheet title, sheet number, scale,
paper size, issue stage, current revision with the last four revisions and
their descriptions, drawn/checked/approved initials, a north point on plans,
"Do not scale", notes and a copyright line. The scale is picked automatically
(1:20 to 1:500) so the largest level fits. The title block layout will be
matched to the office's own title box once that file is supplied
(`_title_block` in `app/dxf_writer.py`).

## Pricing readiness

Besides errors (which block generation) and warnings, the review step lists
**pricing** items. These are the things a quantity surveyor would otherwise
have to ask before pricing (from the 6135 KSA Salon tender checklist):

- client or site address missing;
- rooms without a number, or without floor, wall or ceiling finish codes;
- finish codes not in the schedule, or with no material;
- non-rectangular rooms that need manual setting-out dimensions;
- furniture, doors and fixtures not marked New / Reuse / Modify / Relocate / Remove;
- new items without a material;
- AC units without capacity, outdoor unit location or mounting height
  ("by MEP" is accepted).

Register rules are checked too: revisions are letters before IFC and numbers
from IFC, the current revision must match the revision history, checked-by is
needed from IFT, and approved-by from IFC.

### DXF structure (for CAD users)
- AutoCAD 2018 format, millimetres, AIA-style layers: `A-WALL`, `A-WALL-PATT`,
  `A-DOOR`, `A-DOOR-HEAD`, `A-GLAZ`, `A-DOOR-IDEN`, `A-FLOR-LEVL` (platform
  edges), `A-FLOR-OPEN` (voids), `A-ANNO-DIMS`, `A-ANNO-DIMS-INTR`,
  `A-AREA-IDEN`, `G-ROOM-NAME`, `I-FURN`, `I-FURN-IDEN`, `I-CLNG`,
  `I-CLNG-IDEN`, `I-FLOR-PATT`, `I-FLOR-IDEN`, `M-HVAC-EQPM`, `M-HVAC-IDEN`,
  `M-HVAC-DIMS`, `G-ANNO-TTLB`.
- Levels are laid out side by side in model space, 20 m apart.
- The CAD package download is a ZIP of the DXF plus `aalia_logo.png`. The DXF
  references the logo by file name, so keep the two files together.
- Everything is drawn once in model space at 1:1. Each sheet is a paper-space
  layout whose locked viewport freezes the layers that sheet should not show.
  This is the standard office workflow, so a CAD technician can carry on
  drafting directly.
- Dimensions are real associative-style DIMENSION entities using the `ADC-ARCH`
  dimension style (architectural ticks).
- The PDF is plotted from the same DXF layouts, so the two always match.

## Running it

```bash
cd cad_drawing_system
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # needed only for AI extraction
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. Without an API key you can still use
**Load sample apartment** to see the full workflow and output.

Tests: `python -m pytest -q`

A sample model and its generated DXF are in `samples/`. PDFs are excluded by
the repository's `.gitignore`; generate the sample PDF from the web UI
(*Load sample apartment* → *Generate drawing set*).

## Workflow

1. **Upload**: floor plans (PNG/JPG/WEBP/PDF, up to 25 MB each) and optional
   renders or mood images.
2. **Extract**: Claude (`claude-opus-5-5`, structured output) reads the plan.
   It sets the scale from dimension strings or a scale bar, or failing that
   from standard object sizes. It returns wall centrelines, openings, rooms,
   furniture (from the plan and the renders), a proposed ceiling layout and
   finishes. Anything it is unsure of goes into *AI notes & assumptions*. Add a
   brief with known facts ("overall width 12.0 m", "exterior walls 200 mm") and
   it treats those as correct. The request uses server-side refusal fallback
   (`fallbacks: "default"`), so a declined request is retried on a fallback
   model automatically.
3. **Snap**: the geometry is cleaned up automatically. Near-straight walls
   are straightened, coordinates within 30 mm are aligned, corners are closed
   and T-junctions are snapped onto their walls.
4. **Review**: live plan preview, editable tables for every element, and an
   issues list. **Errors** (openings past wall ends or overlapping, duplicate
   IDs, invalid room shapes) block generation. **Warnings** (unusual wall
   thickness or door width, rooms without a door, furniture clashing with
   walls, unusual ceiling heights, unknown finish codes) are for judgement.
5. **Generate**: download the DXF and PDF and preview every sheet.

## Important limitations

- **The AI output is a draft, not a survey.** Accuracy depends on the input:
  a clean plan with full dimension strings gives good results; photos of
  rooms or renders alone cannot give reliable dimensions. Always check
  dimensions against the source, and on site.
- The drawings carry the status *PRELIMINARY - NOT FOR CONSTRUCTION* and a
  note that a qualified professional must check and approve them. Change the
  status in *Project info* only after that review.
- Walls are straight segments; curved walls are not supported yet.
- Covered: plans (Series 02), the RCP (ID-301), HVAC coordination (ID-405) and
  schedules (Series 09). Not yet generated: front matter (Series 00),
  existing and demolition plans (Series 01), lighting layout and switching,
  electrical and plumbing points, elevations, sections, details and joinery
  (Series 05-08). The model already holds wall, sill, head and ceiling heights
  for the elevation and section work.
- AC pipe routes, condensate drains and isolators are not drawn yet; the
  schedule records capacity and outdoor location for the MEP engineer.
- A continuation page of a long schedule is numbered with a page suffix
  (e.g. ID-913.2).
- Files are stored on local disk (`CAD_DATA_DIR`, default `./data`). There is
  no login, so run it on a trusted network or put it behind the company's
  authentication before exposing it.

## Code map

| File | Purpose |
|---|---|
| `app/schema.py` | Building model (Pydantic) and the strict JSON schema sent to Claude |
| `app/extraction.py` | Image preparation, prompt and Claude call |
| `app/geometry.py` | Snapping, wall outlines with openings cut (Shapely), net room areas |
| `app/validation.py` | Errors and warnings shown in review |
| `app/dxf_writer.py` | Layers, symbols, dimensions, sheets, title block, legends, schedules, register numbering and stage rules |
| `app/assets/aalia_logo.png` | Company logo used in the title block |
| `app/pdf_writer.py` | Plots the DXF layouts to PDF/PNG (matplotlib, no AGPL dependencies) |
| `app/main.py` | FastAPI endpoints and file storage |
| `app/static/` | Web UI (plain HTML/JS, no build step) |
