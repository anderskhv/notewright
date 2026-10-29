"""HTTP API and static files. Run with `python -m notewright`."""

import os
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import pipeline

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="Notewright")


class UrlJob(BaseModel):
    url: str
    mode: str = "piano"


def _mode(mode: str) -> str:
    if mode not in ("piano", "band"):
        raise HTTPException(400, "mode must be 'piano' or 'band'")
    return mode


@app.on_event("startup")
def startup() -> None:
    pipeline.start_worker()
    pipeline.resume_pending()
    if os.environ.get("NOTEWRIGHT_OPEN"):
        # The socket opens just after startup; give it a moment before the browser asks.
        threading.Timer(1.0, webbrowser.open, [os.environ["NOTEWRIGHT_OPEN"]]).start()


@app.get("/api/jobs")
def jobs():
    return pipeline.list_jobs()


@app.post("/api/jobs")
def create(job: UrlJob):
    if not job.url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a full link, starting with https://")
    return pipeline.submit(_mode(job.mode), url=job.url.strip())


@app.post("/api/jobs/upload")
async def upload(file: UploadFile = File(...), mode: str = Form("piano")):
    return pipeline.submit(_mode(mode), upload=(file.filename or "upload", await file.read()))


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    record = pipeline.read_job(job_id)
    if not record:
        raise HTTPException(404)
    return record


@app.delete("/api/jobs/{job_id}")
def delete(job_id: str):
    pipeline.delete_job(job_id)
    return {"ok": True}


@app.get("/files/{job_id}/{path:path}")
def files(job_id: str, path: str):
    base = (pipeline.jobs_dir() / job_id).resolve()
    target = (base / path).resolve()
    if base.parent != pipeline.jobs_dir().resolve() or base not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
