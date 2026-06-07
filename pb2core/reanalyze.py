"""Re-analyze the unprocessed training backlog with a chosen/active model.

Shares the exact routing logic used by ingestion stage 4 (see
:mod:`pb2core.inference`) so a frame routed at ingest time and one re-routed
after a model upgrade follow identical rules. Processed (human-verified) frames
are never touched.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from pb2core.config import get_runtime_settings
from pb2core.db.models import Frame, Label
from pb2core.inference import load_active_detector
from pb2core.storage import storage


def reanalyze_training_backlog(
    db, model_name_or_version: str | None = None, dry_run: bool = False
) -> int:
    settings = get_runtime_settings(db)
    detector, model = load_active_detector(db, settings, model_name_or_version)
    if model is None:
        raise ValueError("no model available to reanalyze with")
    if detector is None:
        raise ValueError("could not load weights for the selected model")

    frames = db.execute(
        select(Frame).where(Frame.queue == "training", Frame.status == "unprocessed")
    ).scalars().all()

    moved = 0
    for f in frames:
        try:
            box = detector.detect(storage.absolute(storage.frame_path(f.video_id, f.id)))
        except Exception:  # noqa: BLE001 - skip a bad frame, keep going
            box = None
        if box is None:
            continue
        moved += 1
        if dry_run:
            continue
        f.queue = "validation"
        f.model_id = model.id
        db.execute(delete(Label).where(Label.frame_id == f.id, Label.source == "model"))
        db.add(
            Label(
                frame_id=f.id,
                source="model",
                class_id=0,
                x_center=box[0],
                y_center=box[1],
                width=box[2],
                height=box[3],
                confidence=round(box[4], 4),
            )
        )

    if not dry_run:
        db.commit()
    return moved
