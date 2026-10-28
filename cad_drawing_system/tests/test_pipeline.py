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


def test_sample_model_has_no_issues(model):
    assert validation.validate(model) == []


def test_drawing_set_is_valid_dxf_and_pdf(model, tmp_path):
    doc, names, scale = dxf_writer.build_dxf(model)
    assert scale == 50
    assert names[:4] == ["A-101 General arrangement plan", "ID-101 Furniture layout plan",
                         "ID-102 Reflected ceiling plan", "ID-103 Flooring plan"]
    assert all(n.startswith("A-60") for n in names[4:])
    dxf = tmp_path / "set.dxf"
    doc.saveas(dxf)
    reloaded = ezdxf.readfile(dxf)
    auditor = reloaded.audit()
    assert not auditor.has_errors, [e.message for e in auditor.errors]
    assert "Layout1" not in reloaded.layouts

    # each plan sheet shows only its own layers
    rcp = reloaded.layouts.get("ID-102 Reflected ceiling plan")
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
    assert r.status_code == 200 and r.json()["issues"] == []

    broken = json.loads(json.dumps(model))
    broken["openings"][0]["offset"] = 99999
    c.put(f"/api/projects/{pid}/model", json=broken)
    assert c.post(f"/api/projects/{pid}/generate").status_code == 422
    c.put(f"/api/projects/{pid}/model", json=model)

    r = c.post(f"/api/projects/{pid}/generate")
    assert r.status_code == 200, r.text
    outputs = r.json()["outputs"]
    assert sorted(outputs) == ["Villa_Fit-out.dxf", "Villa_Fit-out.pdf"]
    pdf = c.get(f"/api/projects/{pid}/files/Villa_Fit-out.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert c.get(f"/api/projects/{pid}/files/..%2Fproject.json").status_code == 404
    assert c.get("/api/projects/notanid").status_code == 404
