from sqlalchemy import delete, select

from pb2core.db.models import Frame, Label, Model
from pb2core.ingest import _fake_ball_box


def reanalyze_training_backlog(db, model_name_or_version: str | None = None, dry_run: bool = False) -> int:
    active = None
    if model_name_or_version:
        active = db.execute(select(Model).where(Model.name == model_name_or_version)).scalar_one_or_none()
        if active is None and model_name_or_version.isdigit():
            active = db.execute(select(Model).where(Model.version == int(model_name_or_version))).scalar_one_or_none()
    if active is None:
        active = db.execute(select(Model).where(Model.is_active.is_(True))).scalar_one()

    frames = db.execute(select(Frame).where(Frame.queue == "training", Frame.status == "unprocessed")).scalars().all()
    moved = 0
    for f in frames:
        box = _fake_ball_box(f.frame_index)
        if box is None:
            continue
        moved += 1
        if dry_run:
            continue
        f.queue = "validation"
        f.model_id = active.id
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
                confidence=0.7,
            )
        )
    if not dry_run:
        db.commit()
    return moved
