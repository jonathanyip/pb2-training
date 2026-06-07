"""Ingestion pipeline: acquire -> probe -> sample -> pre-label & route.

Stages mirror docs/ingestion-pipeline.md. Heavy lifting uses yt-dlp (YouTube
download), ffprobe/ffmpeg (probe + frame sampling) and the shared LibreYOLO
detector (pre-labeling). Pre-labeling uses the exact same routing code as
``pb2 reanalyze`` via :mod:`pb2core.inference`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import delete, func, select, update

from pb2core.config import get_runtime_settings
from pb2core.db.models import Frame, IngestJob, Label, Video
from pb2core.inference import load_active_detector
from pb2core.storage import storage


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _parse_frame_rate(value: str | None) -> float:
    if not value or value in ("0/0", "N/A"):
        return 0.0
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe(source: Path) -> dict:
    """Read fps, duration and dimensions with ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate,r_frame_rate,width,height",
        "-show_entries", "format=duration",
        "-of", "json", str(source),
    ]
    data = json.loads(_run(cmd).stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    fps = _parse_frame_rate(stream.get("avg_frame_rate")) or _parse_frame_rate(
        stream.get("r_frame_rate")
    )
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "fps": fps,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": duration,
    }


def _jpeg_quality_to_qv(quality: int) -> int:
    """Map a 0-100 JPEG quality onto ffmpeg's mjpeg -q:v scale (2=best..31)."""
    quality = max(0, min(100, int(quality)))
    return max(2, min(31, round(31 - (quality / 100.0) * 29)))


def _sampling_plan(settings: dict, src_fps: float) -> tuple[str, float]:
    """Return (ffmpeg video filter, seconds-between-sampled-frames)."""
    mode = settings.get("sampling.mode", "every_n_frames")
    if mode == "fps":
        rate = float(settings.get("sampling.target_fps", 2) or 2)
        rate = rate if rate > 0 else 2.0
        return f"fps={rate}", 1.0 / rate
    if mode == "interval_seconds":
        interval = float(settings.get("sampling.interval_seconds", 0.5) or 0.5)
        interval = interval if interval > 0 else 0.5
        return f"fps=1/{interval}", interval
    # default: every_n_frames
    n = max(1, int(settings.get("sampling.every_n_frames", 15) or 15))
    step_dt = (n / src_fps) if src_fps > 0 else 0.0
    return f"select=not(mod(n\\,{n}))", step_dt


def _sample_frames(
    source: Path, out_dir: Path, settings: dict, src_fps: float
) -> list[tuple[int, float, Path]]:
    """Extract frames with ffmpeg. Returns [(frame_index, timestamp_s, path)]."""
    vf, step_dt = _sampling_plan(settings, src_fps)
    quality = int(settings.get("sampling.jpeg_quality", 90) or 90)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", vf,
        "-fps_mode", "vfr",
        "-q:v", str(_jpeg_quality_to_qv(quality)),
    ]
    max_frames = settings.get("sampling.max_frames_per_video")
    if max_frames:
        # Applied after the sampling filter, so it caps the sampled output.
        cmd += ["-frames:v", str(int(max_frames))]
    cmd += [str(out_dir / "frame_%06d.jpg")]
    _run(cmd)

    out: list[tuple[int, float, Path]] = []
    for idx, path in enumerate(sorted(out_dir.glob("frame_*.jpg"))):
        out.append((idx, idx * step_dt, path))
    return out


def _parse_rate_limit(value) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().upper()
    mult = 1
    if text.endswith("K"):
        mult, text = 1024, text[:-1]
    elif text.endswith("M"):
        mult, text = 1024 * 1024, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


def _download_youtube(db, video: Video, source: Path, settings: dict) -> None:
    """Download the source video with yt-dlp and store it at ``source``."""
    if source.exists() and source.stat().st_size > 1024:
        return

    import yt_dlp

    ydl_opts = {
        "format": settings.get("ingestion.youtube.format") or "bestvideo[ext=mp4]/best",
        "outtmpl": str(source.parent / "source_dl.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
    }
    rate = _parse_rate_limit(settings.get("ingestion.youtube.rate_limit"))
    if rate:
        ydl_opts["ratelimit"] = rate

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video.source_url, download=True)
        downloaded = Path(ydl.prepare_filename(info))

    if not downloaded.exists():
        candidates = sorted(source.parent.glob("source_dl.*"))
        if not candidates:
            raise RuntimeError("yt-dlp did not produce an output file")
        downloaded = candidates[0]

    if source.exists():
        source.unlink()
    shutil.move(str(downloaded), str(source))

    title = (info or {}).get("title")
    if title and (not video.title or video.title == video.source_url):
        video.title = title
    db.commit()


def _clear_frames(db, video_id: str) -> None:
    frame_ids = select(Frame.id).where(Frame.video_id == video_id)
    db.execute(delete(Label).where(Label.frame_id.in_(frame_ids)))
    db.execute(delete(Frame).where(Frame.video_id == video_id))
    db.commit()


def process_ingest_job(db, job: IngestJob) -> None:
    settings = get_runtime_settings(db)
    video = db.execute(select(Video).where(Video.id == job.video_id)).scalar_one()

    # 1. Acquire
    video.status = "downloading"
    job.kind = "download"
    job.progress = 0.1
    db.commit()

    source = storage.absolute(storage.video_source_path(video.id, video.container))
    source.parent.mkdir(parents=True, exist_ok=True)
    if video.source_type == "youtube":
        _download_youtube(db, video, source, settings)
    elif not source.exists():
        raise FileNotFoundError(f"uploaded source missing for video {video.id}")

    # 2. Probe
    video.status = "sampling"
    job.kind = "probe"
    job.progress = 0.25
    db.commit()

    info = _probe(source)
    src_fps = info["fps"] or 30.0
    video.fps = info["fps"] or None
    video.duration_s = info["duration"] or None
    width = info["width"] or 1280
    height = info["height"] or 720

    # 3. Sample frames
    job.kind = "sample"
    job.progress = 0.4
    db.commit()

    _clear_frames(db, video.id)
    with tempfile.TemporaryDirectory() as tmp:
        sampled = _sample_frames(source, Path(tmp), settings, src_fps)
        for frame_index, timestamp_s, tmp_path in sampled:
            frame = Frame(
                video_id=video.id,
                frame_index=frame_index,
                timestamp_s=timestamp_s,
                width=width,
                height=height,
                queue="training",
                status="unprocessed",
            )
            db.add(frame)
            db.flush()
            dst = storage.absolute(storage.frame_path(video.id, frame.id))
            shutil.move(str(tmp_path), str(dst))

    video.frame_count = db.execute(
        select(func.count()).select_from(Frame).where(Frame.video_id == video.id)
    ).scalar_one()
    db.commit()

    # 4. Pre-label & route (same routing code as `pb2 reanalyze`)
    video.status = "labeling"
    job.kind = "label"
    job.progress = 0.7
    db.commit()

    detector, model = load_active_detector(db, settings)
    frames = db.execute(select(Frame).where(Frame.video_id == video.id)).scalars().all()
    for frame in frames:
        frame.model_id = model.id if model else None
        box = None
        if detector is not None:
            try:
                box = detector.detect(storage.absolute(storage.frame_path(video.id, frame.id)))
            except Exception:  # noqa: BLE001 - a bad frame must not kill the job
                box = None
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
                    confidence=round(box[4], 4),
                )
            )

    video.status = "ready"
    job.state = "done"
    job.progress = 1.0
    db.commit()


def run_one_queued_job(db) -> bool:
    job = db.execute(
        select(IngestJob).where(IngestJob.state == "queued").order_by(IngestJob.created_at)
    ).scalars().first()
    if job is None:
        return False

    # Atomic claim so concurrent workers can't both grab the same job.
    claimed = db.execute(
        update(IngestJob)
        .where(IngestJob.id == job.id, IngestJob.state == "queued")
        .values(state="running")
    )
    db.commit()
    if claimed.rowcount == 0:
        return True  # another worker claimed it; try again next tick
    db.refresh(job)

    try:
        process_ingest_job(db, job)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job.state = "failed"
        job.error = str(exc)
        if job.video_id:
            v = db.execute(select(Video).where(Video.id == job.video_id)).scalar_one_or_none()
            if v:
                v.status = "failed"
                v.error = str(exc)
        db.commit()
    return True
