from __future__ import annotations

import random
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
    frames = db.execute(q).scalars().all()

    for f in frames:
        split = "val" if random.random() < val_split else "train"
        src = storage.absolute(storage.frame_path(f.video_id, f.id))
        dst = base / "images" / split / f"{f.id}.jpg"
        shutil.copyfile(src, dst)

        labels = db.execute(select(Label).where(Label.frame_id == f.id, Label.source == "human")).scalars().all()
        txt = base / "labels" / split / f"{f.id}.txt"
        with txt.open("w", encoding="utf-8") as out:
            for l in labels:
                out.write(f"0 {l.x_center} {l.y_center} {l.width} {l.height}\n")

    (base / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\nnames: [ball]\n",
        encoding="utf-8",
    )
    return export_id
