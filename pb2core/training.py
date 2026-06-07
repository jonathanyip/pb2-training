"""Model training (``pb2 train``) with LibreYOLO.

Incrementally trains a new named model on only the frames its parent (and the
parent's ancestors) never trained on, then fine-tunes from the parent's weights.
See docs/cli.md.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select

from pb2core.config import get_runtime_settings
from pb2core.dataset import export_dataset
from pb2core.db.models import Frame, Model, ModelTrainedFrame
from pb2core.inference import model_weights_ref
from pb2core.storage import storage


def _lineage_ids(db, model: Model | None) -> set[str]:
    ids: set[str] = set()
    current = model
    while current is not None:
        ids.add(current.id)
        if current.base_model_id:
            current = db.execute(
                select(Model).where(Model.id == current.base_model_id)
            ).scalar_one_or_none()
        else:
            current = None
    return ids


def _train_device(value) -> str:
    """LibreYOLO's trainer treats an empty string as auto-detect."""
    text = str(value or "").strip().lower()
    return "" if text in ("", "auto") else str(value)


def _resolve_base_weights(db, parent: Model | None, full: bool, settings: dict) -> str:
    fallback = settings.get("training.base_weights") or "LibreYOLO9t.pt"
    if full or parent is None:
        return fallback
    return model_weights_ref(parent) or fallback


def train_model(db, name: str, parent_name_or_version: str | None, full: bool = False) -> Model:
    settings = get_runtime_settings(db)

    parent = None
    if parent_name_or_version is not None:
        parent = db.execute(
            select(Model).where(Model.name == parent_name_or_version)
        ).scalar_one_or_none()
        if parent is None and parent_name_or_version.isdigit():
            parent = db.execute(
                select(Model).where(Model.version == int(parent_name_or_version))
            ).scalar_one_or_none()

    eligible = {
        f.id for f in db.execute(select(Frame).where(Frame.status == "processed")).scalars().all()
    }

    if full:
        new_frames = eligible
    else:
        known: set[str] = set()
        if parent is not None:
            for mid in _lineage_ids(db, parent):
                rows = db.execute(
                    select(ModelTrainedFrame.frame_id).where(ModelTrainedFrame.model_id == mid)
                ).all()
                known.update(r[0] for r in rows)
        new_frames = eligible - known

    if not new_frames:
        raise ValueError("nothing new to train on")
    if len(new_frames) < 2:
        raise ValueError("need at least 2 labeled frames to train (for a train/val split)")

    export_id = export_dataset(db, new_frames)
    dataset_yaml = storage.absolute(storage.dataset_dir(export_id)) / "data.yaml"

    base_weights = _resolve_base_weights(db, parent, full, settings)

    max_version = db.execute(select(Model.version).order_by(Model.version.desc())).scalars().first()
    version = 0 if max_version is None else max_version + 1
    run_name = f"v{version:04d}"
    runs_dir = storage.absolute(Path("runs"))

    from libreyolo import LibreYOLO

    model = LibreYOLO(base_weights)
    results = model.train(
        data=str(dataset_yaml),
        epochs=int(settings.get("training.epochs", 100)),
        imgsz=int(settings.get("training.imgsz", 640)),
        batch=int(settings.get("training.batch", 16)),
        device=_train_device(settings.get("training.device")),
        workers=int(settings.get("training.workers", 0) or 0),
        project=str(runs_dir),
        name=run_name,
        exist_ok=True,
    )

    best = _best_checkpoint(results)
    if best is None or not best.exists():
        raise RuntimeError("training did not produce best weights")

    model_path = storage.model_path(version)
    shutil.copyfile(best, storage.absolute(model_path))

    metrics = _extract_metrics(results)

    m = Model(
        name=name,
        version=version,
        path=str(model_path),
        is_active=False,
        is_bootstrap=False,
        base_model_id=parent.id if parent else None,
        trained_from_export_id=export_id,
        metrics=metrics,
    )
    db.add(m)
    db.flush()
    for fid in new_frames:
        db.add(ModelTrainedFrame(model_id=m.id, frame_id=fid))
    db.commit()
    return m


def _best_checkpoint(results) -> Path | None:
    if not isinstance(results, dict):
        return None
    best = results.get("best_checkpoint")
    if best:
        return Path(best)
    save_dir = results.get("save_dir")
    if save_dir:
        candidate = Path(save_dir) / "weights" / "best.pt"
        if candidate.exists():
            return candidate
        last = Path(save_dir) / "weights" / "last.pt"
        if last.exists():
            return last
    return None


def _extract_metrics(results) -> dict:
    metrics: dict[str, float] = {}
    if not isinstance(results, dict):
        return metrics
    if results.get("best_mAP50") is not None:
        metrics["map50"] = round(float(results["best_mAP50"]), 4)
    if results.get("best_mAP50_95") is not None:
        metrics["map50_95"] = round(float(results["best_mAP50_95"]), 4)
    return metrics
