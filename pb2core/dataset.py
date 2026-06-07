from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from pb2core.config import get_runtime_settings
from pb2core.db.models import Frame, Label
from pb2core.storage import storage


def export_dataset(db, frame_ids: set[str] | None = None) -> str:
    settings = get_runtime_settings(db)
    val_split = float(settings.get("training.val_split", 0.15))
    export_id = str(uuid.uuid4())
    base = storage.absolute(storage.dataset_dir(export_id))
    for p in ["images/train", "images/val", "labels/train", "labels/val"]:
        (base / p).mkdir(parents=True, exist_ok=True)

    q = select(Frame).where(Frame.status == "processed")
    if frame_ids is not None:
        if not frame_ids:
            return export_id
        q = q.where(Frame.id.in_(frame_ids))
    # Deterministic ordering so the train/val split is reproducible and so we
    # can guarantee at least one image lands in each split (a random split can
    # otherwise produce an empty val set for small incremental batches and
    # crash training).
    frames = sorted(db.execute(q).scalars().all(), key=lambda f: f.id)

    n = len(frames)
    n_val = int(round(n * val_split))
    if n >= 2:
        n_val = min(max(n_val, 1), n - 1)
    else:
        n_val = 0

    for i, f in enumerate(frames):
        split = "val" if i < n_val else "train"
        src = storage.absolute(storage.frame_path(f.video_id, f.id))
        dst = base / "images" / split / f"{f.id}.jpg"
        shutil.copyfile(src, dst)

        labels = db.execute(select(Label).where(Label.frame_id == f.id, Label.source == "human")).scalars().all()
        txt = base / "labels" / split / f"{f.id}.txt"
        with txt.open("w", encoding="utf-8") as out:
            for l in labels:
                out.write(f"0 {l.x_center} {l.y_center} {l.width} {l.height}\n")

    (base / "data.yaml").write_text(
        f"path: {base}\ntrain: images/train\nval: images/val\nnc: 1\nnames: [ball]\n",
        encoding="utf-8",
    )
    return export_id
