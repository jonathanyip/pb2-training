from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import delete, select

from pb2core.config import get_runtime_settings
from pb2core.db.models import Frame, IngestJob, Label, Model, Video
from pb2core.storage import storage


def _fake_ball_box(frame_index: int) -> tuple[float, float, float, float] | None:
    if frame_index % 2 == 0:
        cx = 0.45 + (frame_index % 5) * 0.02
        cy = 0.45
        return (cx, cy, 0.05, 0.05)
    return None


def _generate_frame_image(path: Path, idx: int, width: int = 1280, height: int = 720) -> None:
    img = Image.new("RGB", (width, height), (20 + (idx * 13) % 180, 130, 70))
    d = ImageDraw.Draw(img)
    d.text((10, 10), f"frame {idx}", fill=(255, 255, 255))
    img.save(path, quality=90)


def process_ingest_job(db, job: IngestJob) -> None:
    settings = get_runtime_settings(db)
    video = db.execute(select(Video).where(Video.id == job.video_id)).scalar_one()

    video.status = "downloading"
    job.kind = "download"
    job.progress = 0.1
    db.commit()

    source = storage.absolute(storage.video_source_path(video.id, video.container))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("source placeholder\n", encoding="utf-8")

    video.status = "sampling"
    job.kind = "sample"
    job.progress = 0.3
    db.commit()

    frame_total = settings.get("sampling.max_frames_per_video") or 20
    fps = 30.0
    video.fps = fps
    video.duration_s = frame_total / max(fps, 1)

    db.execute(delete(Frame).where(Frame.video_id == video.id))
    db.commit()

    width, height = 1280, 720
    for i in range(frame_total):
        frame = Frame(
            video_id=video.id,
            frame_index=i,
            timestamp_s=i / fps,
            width=width,
            height=height,
            queue="training",
            status="unprocessed",
        )
        db.add(frame)
        db.flush()
        frame_path = storage.absolute(storage.frame_path(video.id, frame.id))
        _generate_frame_image(frame_path, i, width, height)

    video.frame_count = frame_total
    db.commit()

    video.status = "labeling"
    job.kind = "label"
    job.progress = 0.6
    db.commit()

    active_model = db.execute(select(Model).where(Model.is_active.is_(True))).scalar_one_or_none()
    if active_model is None:
        active_model = db.execute(select(Model).order_by(Model.version.desc())).scalars().first()

    frames = db.execute(select(Frame).where(Frame.video_id == video.id)).scalars().all()
    for frame in frames:
        box = _fake_ball_box(frame.frame_index)
        frame.model_id = active_model.id if active_model else None
        if box is None:
            frame.queue = "training"
        else:
            frame.queue = "validation"
            db.add(
                Label(
                    frame_id=frame.id,
                    source="model",
                    class_id=0,
                    x_center=box[0],
                    y_center=box[1],
                    width=box[2],
                    height=box[3],
                    confidence=round(0.6 + random.random() * 0.3, 3),
                )
            )
    video.status = "ready"
    job.state = "done"
    job.progress = 1.0
    db.commit()


def run_one_queued_job(db) -> bool:
    job = db.execute(select(IngestJob).where(IngestJob.state == "queued").order_by(IngestJob.created_at)).scalars().first()
    if job is None:
        return False
    job.state = "running"
    db.commit()
    try:
        process_ingest_job(db, job)
    except Exception as exc:  # noqa: BLE001
        job.state = "failed"
        job.error = str(exc)
        if job.video_id:
            v = db.execute(select(Video).where(Video.id == job.video_id)).scalar_one_or_none()
            if v:
                v.status = "failed"
                v.error = str(exc)
        db.commit()
    return True
