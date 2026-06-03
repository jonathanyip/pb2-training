from __future__ import annotations

from sqlalchemy import select

from pb2core.dataset import export_dataset
from pb2core.db.models import Frame, Model, ModelTrainedFrame
from pb2core.storage import storage


def _lineage_ids(db, model: Model | None) -> set[str]:
    ids: set[str] = set()
    current = model
    while current is not None:
        ids.add(current.id)
        if current.base_model_id:
            current = db.execute(select(Model).where(Model.id == current.base_model_id)).scalar_one_or_none()
        else:
            current = None
    return ids


def train_model(db, name: str, parent_name_or_version: str | None, full: bool = False) -> Model:
    parent = None
    if parent_name_or_version is not None:
        parent = db.execute(select(Model).where(Model.name == parent_name_or_version)).scalar_one_or_none()
        if parent is None and parent_name_or_version.isdigit():
            parent = db.execute(select(Model).where(Model.version == int(parent_name_or_version))).scalar_one_or_none()

    eligible = {f.id for f in db.execute(select(Frame).where(Frame.status == "processed")).scalars().all()}

    if full:
        new_frames = eligible
    else:
        known: set[str] = set()
        if parent is not None:
            lineage = _lineage_ids(db, parent)
            for mid in lineage:
                rows = db.execute(select(ModelTrainedFrame.frame_id).where(ModelTrainedFrame.model_id == mid)).all()
                known.update(r[0] for r in rows)
        new_frames = eligible - known

    if not new_frames:
        raise ValueError("nothing new to train on")

    export_id = export_dataset(db, new_frames)
    max_version = db.execute(select(Model.version).order_by(Model.version.desc())).scalars().first()
    version = 0 if max_version is None else max_version + 1
    model_path = storage.model_path(version)
    storage.absolute(model_path).write_text(
        f"trained model {name} from {parent.name if parent else 'scratch'} export {export_id}\n",
        encoding="utf-8",
    )

    m = Model(
        name=name,
        version=version,
        path=str(model_path),
        is_active=False,
        is_bootstrap=False,
        base_model_id=parent.id if parent else None,
        trained_from_export_id=export_id,
        metrics={"map50": 0.5},
    )
    db.add(m)
    db.flush()
    for fid in new_frames:
        db.add(ModelTrainedFrame(model_id=m.id, frame_id=fid))
    db.commit()
    return m
