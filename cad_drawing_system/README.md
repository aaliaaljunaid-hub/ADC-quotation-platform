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

## Drawing set produced

| Sheet | Content |
|---|---|
| A-101 General arrangement plan | Walls with poché, doors with swings, windows, door/window tags, room names and net areas, three exterior dimension chains (openings, building breaks, overall), clear internal room dimensions, area summary |
| ID-101 Furniture layout plan | FF&E symbols (sofas, beds, sanitaryware, kitchen, etc.) with tags |
| ID-102 Reflected ceiling plan | Lighting, AC diffusers/grilles, detectors, exhaust fans; ceiling finish and height per room; symbol legend |
| ID-103 Flooring plan | Floor patterns at true size (tile grid from e.g. "600x600", plank lines, carpet), finish codes, legend |
| A-6xx Schedules | Door & window schedule (with rooms each side), room schedule, finishes schedule, FF&E schedule; continues onto more sheets automatically |

All sheets are A3 landscape with a title block (company, project, client, drawing
number, revision, scale, date, drawn/checked, status), north arrow, scale bar and
standard notes. The scale is picked automatically (1:20 to 1:500) so the plan
fits. The furniture sheet is left out when there is no furniture, and the
ceiling sheet when there are no ceiling fixtures.

### DXF structure (for CAD users)
- AutoCAD 2018 format, millimetres, AIA-style layers: `A-WALL`, `A-WALL-PATT`,
  `A-DOOR`, `A-DOOR-HEAD`, `A-GLAZ`, `A-DOOR-IDEN`, `A-ANNO-DIMS`,
  `A-ANNO-DIMS-INTR`, `A-AREA-IDEN`, `G-ROOM-NAME`, `I-FURN`, `I-FURN-IDEN`,
  `I-CLNG`, `I-CLNG-IDEN`, `I-FLOR-PATT`, `I-FLOR-IDEN`, `G-ANNO-TTLB`.
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
- Version 1 covers plans and schedules. Elevations, sections and joinery
  details are not generated yet. The building model already holds wall
  heights, sill and head heights, and ceiling heights, which those sheets
  would need.
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
| `app/dxf_writer.py` | Layers, symbols, dimensions, sheets, title block, legends, schedules |
| `app/pdf_writer.py` | Plots the DXF layouts to PDF/PNG (matplotlib, no AGPL dependencies) |
| `app/main.py` | FastAPI endpoints and file storage |
| `app/static/` | Web UI (plain HTML/JS, no build step) |
