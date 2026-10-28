"""HTTP API and web UI for the image-to-CAD drawing system.

Workflow per project: upload plans/renders -> AI extraction -> human review
and edits -> validation -> DXF + PDF drawing set.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import dxf_writer, geometry, pdf_writer, validation
from .extraction import ExtractionError, InputImage, extract
from .schema import BuildingModel

APP_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("CAD_DATA_DIR", APP_DIR.parent / "data")) / "projects"
SAMPLE_MODEL = APP_DIR.parent / "samples" / "sample_model.json"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="ADC CAD Drawing System")


# ---------------------------------------------------------------- storage helpers

def _project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", pid):
        raise HTTPException(404, "Project not found")
    d = DATA_DIR / pid
    if not d.is_dir():
        raise HTTPException(404, "Project not found")
    return d


def _read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
    return stem[:80]


def _uploads(d: Path) -> list[dict]:
    meta = _read_json(d / "uploads.json", [])
    return [m for m in meta if (d / "uploads" / m["file"]).exists()]


def _status(d: Path) -> dict:
    meta = _read_json(d / "project.json")
    out = d / "output"
    return {
        **meta,
        "uploads": _uploads(d),
        "has_model": (d / "model.json").exists(),
        "issues": _read_json(d / "issues.json", []),
        "sheets": _read_json(out / "sheets.json", []),
        "outputs": sorted(p.name for p in out.glob("*") if p.suffix in (".dxf", ".pdf")) if out.exists() else [],
    }


def _save_model(d: Path, model: BuildingModel) -> list[dict]:
    issues = [i.to_dict() for i in validation.validate(model)]
    _write_json(d / "model.json", model.model_dump())
    _write_json(d / "issues.json", issues)
    meta = _read_json(d / "project.json")
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(d / "project.json", meta)
    return issues


# ---------------------------------------------------------------- projects

@app.get("/api/projects")
def list_projects():
    if not DATA_DIR.exists():
        return []
    items = [_read_json(p / "project.json") for p in DATA_DIR.iterdir() if (p / "project.json").exists()]
    return sorted(items, key=lambda m: m.get("updated_at", ""), reverse=True)


@app.post("/api/projects")
def create_project(name: str = Body(..., embed=True)):
    pid = uuid.uuid4().hex
    d = DATA_DIR / pid
    (d / "uploads").mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    _write_json(d / "project.json", {"id": pid, "name": name.strip() or "Untitled", "created_at": now, "updated_at": now})
    return _status(d)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _status(_project_dir(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    shutil.rmtree(_project_dir(pid))
    return {"ok": True}


# ---------------------------------------------------------------- uploads

@app.post("/api/projects/{pid}/uploads")
async def upload(pid: str, role: str = Form(...), files: list[UploadFile] = File(...)):
    d = _project_dir(pid)
    if role not in ("plan", "render"):
        raise HTTPException(400, "role must be 'plan' or 'render'")
    meta = _read_json(d / "uploads.json", [])
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"{f.filename}: unsupported file type (use PNG, JPG, WEBP, GIF or PDF)")
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{f.filename}: larger than 25 MB")
        name = f"{uuid.uuid4().hex[:8]}_{_safe_name(f.filename or 'upload' + ext)}"
        (d / "uploads" / name).write_bytes(data)
        meta.append({"file": name, "original": f.filename, "role": role})
    _write_json(d / "uploads.json", meta)
    return _status(d)


@app.get("/api/projects/{pid}/uploads/{name}")
def get_upload(pid: str, name: str):
    path = _project_dir(pid) / "uploads" / _safe_name(name)
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


@app.delete("/api/projects/{pid}/uploads/{name}")
def delete_upload(pid: str, name: str):
    d = _project_dir(pid)
    name = _safe_name(name)
    (d / "uploads" / name).unlink(missing_ok=True)
    _write_json(d / "uploads.json", [m for m in _read_json(d / "uploads.json", []) if m["file"] != name])
    return _status(d)


# ---------------------------------------------------------------- model

@app.post("/api/projects/{pid}/extract")
def run_extraction(pid: str, brief: str = Body("", embed=True)):
    d = _project_dir(pid)
    images = [InputImage(d / "uploads" / m["file"], m["role"]) for m in _uploads(d)]
    try:
        model = extract(images, brief)
    except ExtractionError as e:
        raise HTTPException(422, str(e)) from e
    model = geometry.normalize(model)
    issues = _save_model(d, model)
    return {"model": model.model_dump(), "issues": issues}


@app.post("/api/projects/{pid}/sample")
def load_sample(pid: str):
    """Demo/testing without an API key: load the bundled sample apartment."""
    d = _project_dir(pid)
    model = BuildingModel.model_validate_json(SAMPLE_MODEL.read_text())
    issues = _save_model(d, model)
    return {"model": model.model_dump(), "issues": issues}


@app.get("/api/projects/{pid}/model")
def get_model(pid: str):
    data = _read_json(_project_dir(pid) / "model.json")
    if data is None:
        raise HTTPException(404, "No model yet - run extraction first")
    return data


@app.put("/api/projects/{pid}/model")
def put_model(pid: str, data: dict = Body(...), snap: bool = False):
    d = _project_dir(pid)
    try:
        model = BuildingModel.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    if snap:
        model = geometry.normalize(model)
    issues = _save_model(d, model)
    return {"model": model.model_dump(), "issues": issues}


# ---------------------------------------------------------------- drawings

@app.post("/api/projects/{pid}/generate")
def generate(pid: str):
    d = _project_dir(pid)
    data = _read_json(d / "model.json")
    if data is None:
        raise HTTPException(400, "No model yet - run extraction first")
    model = BuildingModel.model_validate(data)
    issues = validation.validate(model)
    if validation.has_errors(issues):
        raise HTTPException(422, {"message": "Fix the errors before generating drawings",
                                  "issues": [i.to_dict() for i in issues]})
    doc, names, scale = dxf_writer.build_dxf(model)
    out = d / "output"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    slug = _safe_name(model.project.project_name or "drawings").replace(".", "_")
    doc.saveas(out / f"{slug}.dxf")
    pdf_writer.write_pdf(doc, names, str(out / f"{slug}.pdf"))
    sheets = []
    for i, name in enumerate(names):
        png = f"sheet_{i + 1:02d}.png"
        pdf_writer.write_png(doc, name, str(out / png))
        sheets.append({"name": name, "preview": png})
    _write_json(out / "sheets.json", sheets)
    return {**_status(d), "scale": scale}


@app.get("/api/projects/{pid}/files/{name}")
def get_output(pid: str, name: str):
    path = _project_dir(pid) / "output" / _safe_name(name)
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=path.name if path.suffix in (".dxf", ".pdf") else None)


app.mount("/", StaticFiles(directory=APP_DIR / "static", html=True), name="static")
