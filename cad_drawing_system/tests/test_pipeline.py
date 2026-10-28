import io
import json
from pathlib import Path
from types import SimpleNamespace

import ezdxf
import pytest
from PIL import Image

from app import dxf_writer, extraction, geometry, pdf_writer, validation
from app.schema import BuildingModel, json_schema_for_structured_output

SAMPLE = Path(__file__).parent.parent / "samples" / "sample_model.json"


@pytest.fixture
def model() -> BuildingModel:
    return BuildingModel.model_validate_json(SAMPLE.read_text())


def test_sample_model_has_no_errors_or_warnings(model):
    issues = validation.validate(model)
    assert [(i.level, i.item) for i in issues] == [("pricing", "AC-03")]  # left open on purpose in the sample


def test_drawing_set_is_valid_dxf_and_pdf(model, tmp_path):
    model.project.stage = "DD"
    doc, names, scale = dxf_writer.build_dxf(model)
    assert scale == 50
    assert [n.split()[0] for n in names] == ["ID-201", "ID-202", "ID-203", "ID-204", "ID-208", "ID-301", "ID-405",
                                             "ID-901", "ID-902", "ID-905", "ID-906", "ID-913", "ID-917"]
    dxf = tmp_path / "set.dxf"
    doc.saveas(dxf)
    reloaded = ezdxf.readfile(dxf)
    auditor = reloaded.audit()
    assert not auditor.has_errors, [e.message for e in auditor.errors]
    assert "Layout1" not in reloaded.layouts

    # each plan sheet shows only its own layers
    rcp = reloaded.layouts.get("ID-301 Reflected ceiling plan (RCP)")
    vp = next(e for e in rcp if e.dxftype() == "VIEWPORT" and e.dxf.id > 1)
    assert "I-FURN" in vp.frozen_layers and "I-CLNG" not in vp.frozen_layers

    pdf = tmp_path / "set.pdf"
    pdf_writer.write_pdf(doc, names, str(pdf))
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert pdf.read_bytes().count(b"/Type /Page\n") + pdf.read_bytes().count(b"/Type /Page ") >= len(names)


def test_room_areas_are_net_of_walls(model):
    geo = geometry.wall_geometry(model)
    kitchen = next(r for r in model.rooms if r.name == "Kitchen")
    net = geometry.room_net_polygon(kitchen.polygon, geo.solid)
    # 4500 x 5000 on centrelines, minus half of the 100 partition, 200 exterior walls and 150 interior wall
    assert net.area / 1e6 == pytest.approx((4500 - 50 - 100) * (5000 - 100 - 75) / 1e6, rel=1e-3)


def test_normalize_closes_small_gaps():
    m = BuildingModel.model_validate({
        "walls": [
            {"id": "a", "start": {"x": 0, "y": 0}, "end": {"x": 5012, "y": 8}, "thickness": 200, "kind": "exterior"},
            {"id": "b", "start": {"x": 4995, "y": -6}, "end": {"x": 5003, "y": 4000}, "thickness": 200, "kind": "exterior"},
            {"id": "c", "start": {"x": 2500, "y": 40}, "end": {"x": 2500, "y": 4000}, "thickness": 100, "kind": "partition"},
        ],
    })
    n = geometry.normalize(m)
    a, b, c = n.walls
    assert a.start.y == a.end.y  # straightened
    assert (a.end.x, a.end.y) == (b.start.x, b.start.y)  # corner joined
    assert c.start.y == a.start.y  # T-junction pulled onto the wall


def test_validation_flags_bad_openings(model):
    bad = model.model_copy(deep=True)
    bad.openings[0].offset = 11500  # runs past the end of E1
    bad.openings[1].offset = bad.openings[0].offset
    levels = {(i.level, i.message) for i in validation.validate(bad)}
    assert ("error", "Opening runs past the end of its wall.") in levels
    assert validation.has_errors(validation.validate(bad))


def test_structured_output_schema_is_strict():
    schema = json_schema_for_structured_output(BuildingModel)

    def walk(node):
        if isinstance(node, dict):
            assert "default" not in node and "title" not in node
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for k, v in node.items():
                if k in ("properties", "$defs"):
                    for sub in v.values():
                        walk(sub)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    assert "Wall" in schema["$defs"]


class _FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class _FakeClient:
    def __init__(self, text, stop_reason="end_turn"):
        self.calls = []
        response = SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self._response = response

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._response)


def _png(path: Path, size=(3000, 2000)):
    Image.new("RGB", size, "white").save(path)
    return path


def test_extraction_sends_images_and_parses_model(tmp_path, model):
    plan = _png(tmp_path / "plan.png")
    render = _png(tmp_path / "render.jpg", (800, 600))
    client = _FakeClient(model.model_dump_json())
    out = extraction.extract([extraction.InputImage(plan, "plan"), extraction.InputImage(render, "render")],
                             brief="Overall width 12 m", client=client)
    assert out == model
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    blocks = call["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 2
    # large scans are downscaled before upload
    first = Image.open(io.BytesIO(__import__("base64").b64decode(images[0]["source"]["data"])))
    assert max(first.size) == extraction.MAX_IMAGE_EDGE
    assert "Overall width 12 m" in blocks[-1]["text"]


def test_extraction_requires_a_plan(tmp_path):
    with pytest.raises(extraction.ExtractionError):
        extraction.extract([extraction.InputImage(_png(tmp_path / "r.png"), "render")], client=_FakeClient("{}"))


def test_extraction_reports_refusal(tmp_path):
    with pytest.raises(extraction.ExtractionError, match="declined"):
        extraction.extract([extraction.InputImage(_png(tmp_path / "p.png"), "plan")],
                           client=_FakeClient("", stop_reason="refusal"))


def test_api_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CAD_DATA_DIR", str(tmp_path))
    import importlib

    from fastapi.testclient import TestClient

    import app.main as main
    importlib.reload(main)
    c = TestClient(main.app)

    p = c.post("/api/projects", json={"name": "Villa"}).json()
    pid = p["id"]
    buf = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(buf, format="PNG")
    r = c.post(f"/api/projects/{pid}/uploads", data={"role": "plan"}, files=[("files", ("plan.png", buf.getvalue(), "image/png"))])
    assert r.status_code == 200 and len(r.json()["uploads"]) == 1
    assert c.post(f"/api/projects/{pid}/uploads", data={"role": "plan"},
                  files=[("files", ("x.exe", b"MZ", "application/octet-stream"))]).status_code == 400

    assert c.post(f"/api/projects/{pid}/sample").status_code == 200
    model = c.get(f"/api/projects/{pid}/model").json()
    model["project"]["project_name"] = "Villa Fit-out"
    r = c.put(f"/api/projects/{pid}/model", json=model)
    assert r.status_code == 200 and all(i["level"] == "pricing" for i in r.json()["issues"])

    broken = json.loads(json.dumps(model))
    broken["openings"][0]["offset"] = 99999
    c.put(f"/api/projects/{pid}/model", json=broken)
    assert c.post(f"/api/projects/{pid}/generate").status_code == 422
    c.put(f"/api/projects/{pid}/model", json=model)

    r = c.post(f"/api/projects/{pid}/generate")
    assert r.status_code == 200, r.text
    outputs = r.json()["outputs"]
    assert sorted(outputs) == ["Villa_Fit-out.dxf", "Villa_Fit-out.pdf", "Villa_Fit-out_CAD.zip"]
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/projects/{pid}/files/Villa_Fit-out_CAD.zip").content))
    assert sorted(z.namelist()) == ["Villa_Fit-out.dxf", "aalia_logo.png"]
    pdf = c.get(f"/api/projects/{pid}/files/Villa_Fit-out.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert c.get(f"/api/projects/{pid}/files/..%2Fproject.json").status_code == 404
    assert c.get("/api/projects/notanid").status_code == 404


# ---------------------------------------------------------------- register & checklist features

@pytest.mark.parametrize("stage, expected", [
    ("CD", ["ID-202"]),
    ("SD", ["ID-201", "ID-202", "ID-203", "ID-204", "ID-208", "ID-301",
            "ID-901", "ID-902", "ID-905", "ID-906", "ID-913", "ID-917"]),
    ("AP", ["ID-201", "ID-202", "ID-203", "ID-204", "ID-208", "ID-301", "ID-405"]),
])
def test_stage_decides_which_sheets_are_issued(model, stage, expected):
    model.project.stage = stage
    _, names, _ = dxf_writer.build_dxf(model)
    assert [n.split()[0] for n in names] == expected


def test_sheet_number_follows_register(model):
    assert dxf_writer.sheet_number(model, "ID-203") == "ADC-SMP26-ID-203-B"
    assert dxf_writer.level_text(400) == "FFL +0.40"
    assert dxf_writer.level_text(0) == "FFL ±0.00"


def _two_levels(model: BuildingModel) -> BuildingModel:
    ff = model.model_copy(deep=True)
    for coll in ("walls", "rooms", "furniture", "ceiling_fixtures", "ac_units", "openings"):
        for x in getattr(ff, coll):
            x.id = "ff_" + x.id
            if hasattr(x, "level"):
                x.level = "FF"
            if coll == "openings":
                x.wall_id = "ff_" + x.wall_id
                x.tag = "F" + x.tag
            if coll in ("furniture", "ac_units"):
                x.tag = "FF-" + x.tag
                x.room_id = "ff_" + x.room_id
            if coll == "rooms":
                x.number = "1" + x.number
    m = model.model_copy(deep=True)
    m.levels.append(m.level("FF").model_copy(update={"ffl": 4200}))
    for coll in ("walls", "rooms", "furniture", "ceiling_fixtures", "ac_units", "openings"):
        getattr(m, coll).extend(getattr(ff, coll))
    return m


def test_multi_level_sets_get_level_suffixes(model):
    m = _two_levels(model)
    assert m.level_ids() == ["GF", "FF"]
    assert not [i for i in validation.validate(m) if i.level == "error"]
    # snapping works per level: identical plans stacked do not interfere
    assert geometry.normalize(m).walls[0].start == m.walls[0].start
    m.project.stage = "DD"
    doc, names, _ = dxf_writer.build_dxf(m)
    shorts = [n.split()[0] for n in names]
    assert shorts[:4] == ["ID-201.GF", "ID-201.FF", "ID-202.GF", "ID-202.FF"]
    assert "ID-901" in shorts  # one schedule for the whole project
    # the FF model space copy is placed to the right of the GF one
    xs = [e.dxf.start.x for e in doc.modelspace().query("LINE[layer=='A-DOOR-HEAD']")]
    assert max(xs) > 12200 + dxf_writer.LEVEL_GAP


def test_platform_and_void(model, tmp_path):
    from app.schema import Point, Room
    living = next(r for r in model.rooms if r.id == "r1")
    model.rooms += [
        Room(id="p1", name="Pedicure platform", kind="platform", floor_level=400, floor_finish="FL-01",
             polygon=[Point(x=500, y=500), Point(x=2500, y=500), Point(x=2500, y=1500), Point(x=500, y=1500)]),
        Room(id="v1", name="Void", kind="void",
             polygon=[Point(x=5000, y=3500), Point(x=7000, y=3500), Point(x=7000, y=4800), Point(x=5000, y=4800)]),
    ]
    issues = validation.validate(model)
    assert not [i for i in issues if i.item in ("Pedicure platform", "Void") and i.level != "pricing"]
    doc, names, _ = dxf_writer.build_dxf(model)
    texts = [t.dxf.text for t in doc.modelspace().query("TEXT")]
    assert "PEDICURE PLATFORM  FFL +0.40" in texts and "VOID" in texts
    assert living.floor_finish == "FL-02"


def test_pricing_readiness_and_revision_rules(model):
    model.furniture[0].status = ""
    model.project.stage = "IFC"
    model.project.client = ""
    found = {(i.level, i.item) for i in validation.validate(model)}
    assert ("pricing", "F01") in found
    assert ("pricing", "Client") in found
    assert ("warning", "Revision") in found  # letters are not allowed once issued for construction
    assert ("warning", "Approved by") in found
