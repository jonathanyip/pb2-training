from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, func, or_, select

from pb2app.schemas import LabelRequest, SettingsUpdateRequest, YTAddRequest
from pb2core.config import get_bootstrap_config, get_runtime_settings
from pb2core.db.models import Frame, IngestJob, Label, Model, ModelTrainedFrame, Setting, Video
from pb2core.db.session import SessionLocal
from pb2core.ingest import run_one_queued_job
from pb2core.init_db import init_db
from pb2core.storage import storage

API_PREFIX = "/api/v1"

app = FastAPI(title="pb2-training")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

cfg = get_bootstrap_config()
app.add_middleware(CORSMiddleware, allow_origins=cfg["server"]["cors_origins"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
async def startup_event() -> None:
    init_db()

    async def runner():
        while True:
            with SessionLocal() as db:
                had = run_one_queued_job(db)
            await asyncio.sleep(0.5 if had else 1.0)

    asyncio.create_task(runner())


@app.get("/")
def root():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post(f"{API_PREFIX}/videos")
def create_videos(payload: YTAddRequest):
    out = []
    with SessionLocal() as db:
        for url in payload.urls:
            if not url.strip():
                continue
            video = Video(source_type="youtube", source_url=url.strip(), title=url.strip(), status="pending", container="mp4")
            db.add(video)
            db.flush()
            job = IngestJob(video_id=video.id, kind="download", state="queued", payload={"url": url.strip()})
            db.add(job)
            db.flush()
            out.append({"id": video.id, "job_id": job.id, "status": video.status})
        db.commit()
    return {"videos": out}


@app.post(f"{API_PREFIX}/videos/upload")
def upload_video(file: UploadFile = File(...)):
    with SessionLocal() as db:
        video = Video(source_type="upload", original_filename=file.filename or "upload.mp4", title=file.filename or "upload.mp4", status="pending", container="mp4")
        db.add(video)
        db.flush()
        source = storage.absolute(storage.video_source_path(video.id, "mp4"))
        with source.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        job = IngestJob(video_id=video.id, kind="sample", state="queued", payload={"uploaded": True})
        db.add(job)
        db.commit()
        return {"id": video.id, "job_id": job.id, "status": video.status}


@app.get(f"{API_PREFIX}/videos")
def list_videos(page: int = 1, size: int | None = None, q: str | None = None):
    with SessionLocal() as db:
        runtime = get_runtime_settings(db)
        page_size = size or int(runtime.get("ui.pagination_size", 24))
        query = select(Video)
        if q:
            query = query.where(Video.title.ilike(f"%{q}%"))
        total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
        items = db.execute(query.order_by(Video.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).scalars().all()
        out = []
        for v in items:
            tr = db.execute(select(func.count()).select_from(Frame).where(Frame.video_id == v.id, Frame.queue == "training")).scalar_one()
            va = db.execute(select(func.count()).select_from(Frame).where(Frame.video_id == v.id, Frame.queue == "validation")).scalar_one()
            job = db.execute(select(IngestJob).where(IngestJob.video_id == v.id).order_by(IngestJob.created_at.desc())).scalars().first()
            out.append({
                "id": v.id,
                "title": v.title,
                "source_type": v.source_type,
                "frame_count": v.frame_count,
                "status": v.status,
                "queue_breakdown": {"training": tr, "validation": va},
                "created_at": v.created_at.isoformat(),
                "progress": job.progress if job else 0,
                "error": v.error,
            })
    return {"page": page, "size": page_size, "total": total, "items": out}


@app.delete(f"{API_PREFIX}/videos/{{video_id}}")
def delete_video(video_id: str):
    with SessionLocal() as db:
        v = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
        if not v:
            raise HTTPException(status_code=404, detail="video not found")
        db.execute(delete(Label).where(Label.frame_id.in_(select(Frame.id).where(Frame.video_id == video_id))))
        db.execute(delete(Frame).where(Frame.video_id == video_id))
        db.execute(delete(IngestJob).where(IngestJob.video_id == video_id))
        db.delete(v)
        db.commit()
    shutil.rmtree(storage.absolute(storage.video_dir(v.id)), ignore_errors=True)
    shutil.rmtree(storage.absolute(storage.frame_dir(v.id)), ignore_errors=True)
    return {"deleted": video_id}


@app.post(f"{API_PREFIX}/videos/{{video_id}}/retry")
def retry_video(video_id: str):
    with SessionLocal() as db:
        video = db.execute(select(Video).where(Video.id == video_id)).scalar_one_or_none()
        if not video:
            raise HTTPException(status_code=404, detail="video not found")
        video.status = "pending"
        video.error = None
        job = IngestJob(video_id=video.id, kind="download", state="queued", payload={"retry": True})
        db.add(job)
        db.commit()
        return {"job_id": job.id}


@app.get(f"{API_PREFIX}/jobs/{{job_id}}")
def get_job(job_id: str):
    with SessionLocal() as db:
        j = db.execute(select(IngestJob).where(IngestJob.id == job_id)).scalar_one_or_none()
        if not j:
            raise HTTPException(status_code=404, detail="job not found")
        return {"id": j.id, "kind": j.kind, "state": j.state, "progress": j.progress, "error": j.error}


@app.get(f"{API_PREFIX}/frames/next")
def get_next_frame(queue: str = Query(pattern="^(training|validation)$")):
    with SessionLocal() as db:
        frame = db.execute(select(Frame).where(Frame.queue == queue, Frame.status == "unprocessed").order_by(Frame.created_at.asc())).scalars().first()
        if frame is None:
            return {"frame": None, "remaining": 0}
        remaining = db.execute(select(func.count()).select_from(Frame).where(Frame.queue == queue, Frame.status == "unprocessed")).scalar_one()
        video = db.execute(select(Video).where(Video.id == frame.video_id)).scalar_one()
        pre = db.execute(select(Label).where(Label.frame_id == frame.id, Label.source == "model")).scalars().first()
        return {
            "frame": {
                "id": frame.id,
                "video_id": frame.video_id,
                "video_title": video.title,
                "image_url": f"{API_PREFIX}/frames/{frame.id}/image",
                "width": frame.width,
                "height": frame.height,
                "queue": frame.queue,
                "prelabel": None if pre is None else {
                    "class_id": pre.class_id,
                    "x_center": pre.x_center,
                    "y_center": pre.y_center,
                    "width": pre.width,
                    "height": pre.height,
                    "confidence": pre.confidence,
                },
            },
            "remaining": remaining,
        }


@app.get(f"{API_PREFIX}/frames/{{frame_id}}/image")
def get_frame_image(frame_id: str):
    with SessionLocal() as db:
        frame = db.execute(select(Frame).where(Frame.id == frame_id)).scalar_one_or_none()
        if not frame:
            raise HTTPException(status_code=404, detail="frame not found")
        return FileResponse(storage.absolute(storage.frame_path(frame.video_id, frame.id)))


@app.post(f"{API_PREFIX}/frames/{{frame_id}}/label")
def label_frame(frame_id: str, payload: LabelRequest):
    with SessionLocal() as db:
        frame = db.execute(select(Frame).where(Frame.id == frame_id)).scalar_one_or_none()
        if not frame:
            raise HTTPException(status_code=404, detail="frame not found")
        db.execute(delete(Label).where(Label.frame_id == frame.id, Label.source == "human"))
        for b in payload.boxes:
            db.add(Label(frame_id=frame.id, source="human", class_id=0, x_center=b.x_center, y_center=b.y_center, width=b.width, height=b.height, confidence=None))
        frame.status = "processed"
        frame.has_ball = len(payload.boxes) > 0
        db.commit()
        return {"id": frame.id, "status": frame.status, "has_ball": frame.has_ball}


@app.post(f"{API_PREFIX}/frames/{{frame_id}}/reopen")
def reopen_frame(frame_id: str):
    with SessionLocal() as db:
        frame = db.execute(select(Frame).where(Frame.id == frame_id)).scalar_one_or_none()
        if not frame:
            raise HTTPException(status_code=404, detail="frame not found")
        frame.status = "unprocessed"
        frame.has_ball = None
        db.execute(delete(Label).where(Label.frame_id == frame.id, Label.source == "human"))
        db.commit()
        return {"id": frame.id, "status": frame.status}


@app.get(f"{API_PREFIX}/frames/count")
def frame_count(queue: str, status: str):
    with SessionLocal() as db:
        c = db.execute(select(func.count()).select_from(Frame).where(Frame.queue == queue, Frame.status == status)).scalar_one()
        return {"count": c}


@app.get(f"{API_PREFIX}/settings")
def get_settings():
    from pb2core.defaults import DEFAULT_RUNTIME_SETTINGS

    with SessionLocal() as db:
        values = get_runtime_settings(db)

    def infer_type(value) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        return "string"

    schema = {}
    for key, value in values.items():
        default = DEFAULT_RUNTIME_SETTINGS.get(key, value)
        ref = value if value is not None else default
        schema[key] = {"type": infer_type(ref), "group": key.split(".")[0]}
    return {"values": values, "schema": schema, "defaults": DEFAULT_RUNTIME_SETTINGS}


@app.put(f"{API_PREFIX}/settings")
def put_settings(payload: SettingsUpdateRequest):
    blocked = {"storage.", "database.", "server."}
    with SessionLocal() as db:
        updated = []
        for key, value in payload.values.items():
            if any(key.startswith(prefix) for prefix in blocked):
                raise HTTPException(status_code=400, detail=f"bootstrap key not editable: {key}")
            row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
            if row is None:
                row = Setting(key=key, value=value, updated_by="ui")
                db.add(row)
            else:
                row.value = value
                row.updated_by = "ui"
            updated.append(key)
        db.commit()
        return {"updated": updated}


@app.get(f"{API_PREFIX}/models")
def list_models():
    with SessionLocal() as db:
        models = db.execute(select(Model).order_by(Model.version.desc())).scalars().all()
        active = next((m for m in models if m.is_active), None)
        items = []
        for m in models:
            trained_count = db.execute(select(func.count()).select_from(ModelTrainedFrame).where(ModelTrainedFrame.model_id == m.id)).scalar_one()
            items.append({
                "id": m.id,
                "name": m.name,
                "version": m.version,
                "is_active": m.is_active,
                "is_bootstrap": m.is_bootstrap,
                "base_model_id": m.base_model_id,
                "trained_frames": trained_count,
                "metrics": m.metrics,
            })
        return {
            "active": None if active is None else {"id": active.id, "name": active.name, "version": active.version},
            "items": items,
        }


@app.post(f"{API_PREFIX}/models/{{model_id}}/activate")
def activate_model(model_id: str):
    with SessionLocal() as db:
        model = db.execute(select(Model).where(Model.id == model_id)).scalar_one_or_none()
        if not model:
            raise HTTPException(status_code=404, detail="model not found")
        for m in db.execute(select(Model)).scalars().all():
            m.is_active = m.id == model_id
        db.commit()
        return {"id": model.id, "name": model.name, "is_active": model.is_active}
