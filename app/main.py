"""FastAPI app: drop two files, press GO, download a tracked-changes .docx."""

from __future__ import annotations

import shutil
import traceback
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from .pipeline.run import process

APP_DIR = Path(__file__).resolve().parent
JOBS = APP_DIR.parent / "jobs"
JOBS.mkdir(exist_ok=True)

app = FastAPI(title="Red Pen")

_state: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")


def _run(job_id: str, base: Path, scan: Path, out: Path) -> None:
    def progress(msg: str, frac: float) -> None:
        _state[job_id].update(status="running", message=msg, progress=frac)

    try:
        result = process(base, scan, out, progress)
        _state[job_id].update(status="done", message="Ready", progress=1.0, result=result)
    except Exception as exc:  # surface the real reason, don't swallow it
        _state[job_id].update(
            status="error",
            message=f"{type(exc).__name__}: {exc}",
            trace=traceback.format_exc(),
        )


@app.post("/api/jobs")
async def create_job(
    background: BackgroundTasks,
    base: UploadFile = File(...),
    markup: UploadFile = File(...),
):
    if not (base.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "The base document must be a .docx file.")
    if not (markup.filename or "").lower().endswith((".pdf", ".png", ".jpg", ".jpeg")):
        raise HTTPException(400, "The markup must be a scanned PDF or an image.")

    job_id = uuid.uuid4().hex[:12]
    d = JOBS / job_id
    d.mkdir(parents=True)
    base_p, scan_p = d / "base.docx", d / ("markup" + Path(markup.filename).suffix)
    out_p = d / (Path(base.filename).stem + "_REDPEN_TRACKED.docx")
    for up, dest in ((base, base_p), (markup, scan_p)):
        with dest.open("wb") as fh:
            shutil.copyfileobj(up.file, fh)

    _state[job_id] = {"status": "queued", "message": "Queued", "progress": 0.0,
                      "download_name": out_p.name}
    background.add_task(_run, job_id, base_p, scan_p, out_p)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    st = _state.get(job_id)
    if not st:
        raise HTTPException(404, "No such job.")
    return {k: v for k, v in st.items() if k != "trace"}


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    st = _state.get(job_id)
    if not st or st.get("status") != "done":
        raise HTTPException(404, "Not ready.")
    path = JOBS / job_id / st["download_name"]
    if not path.exists():
        raise HTTPException(404, "Output missing.")
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
