"""Shared ball-detection inference.

The same routing logic is used by ingestion stage 4 (pre-label) and the
``pb2 reanalyze`` CLI so the rules can never drift between them.

Important class-id detail: the bootstrap model uses COCO weights and reports a
ball as ``model.ball_class_id`` (32 = "sports ball"). Models trained on our
single-class YOLO dataset report the ball as class ``0``. The detector resolves
the correct source class id per model; labels are always stored internally as
class ``0``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional, Tuple

from sqlalchemy import select

from pb2core.db.models import Model
from pb2core.storage import storage

# (x_center, y_center, width, height, confidence), all normalized to [0, 1].
BallBox = Tuple[float, float, float, float, float]

# A real checkpoint is well over this; the bootstrap placeholder marker is tiny.
_MIN_WEIGHTS_BYTES = 4096


def model_weights_ref(model: Model | None) -> Optional[str]:
    """Resolve a loadable weights reference for a model row.

    Returns a real ``vNNNN.pt`` file path when one exists, otherwise the
    ``base_weights`` reference (a local path or an auto-downloadable LibreYOLO
    checkpoint name). Returns ``None`` when nothing loadable is available.
    """
    if model is None:
        return None
    if model.path:
        # Stored paths are POSIX-relative; normalize any backslashes that may
        # have been written by an older Windows run so they resolve on any OS.
        rel = model.path.replace("\\", "/")
        try:
            p = storage.absolute(Path(rel))
        except ValueError:
            p = Path(rel)
        if p.exists() and p.is_file() and p.stat().st_size > _MIN_WEIGHTS_BYTES:
            return str(p)
    if model.base_weights:
        return model.base_weights
    return None


class BallDetector:
    """Loads a LibreYOLO model once and detects the best ball box per frame."""

    def __init__(
        self,
        weights: str,
        *,
        ball_class_id: int,
        conf: float,
        iou: float,
        imgsz: int,
        device: str,
        half: bool,
    ) -> None:
        from libreyolo import LibreYOLO

        self.ball_class_id = int(ball_class_id)
        self.conf = float(conf)
        self.iou = float(iou)
        self.imgsz = int(imgsz)
        self.half = bool(half)
        self.model = LibreYOLO(weights, device=device or "auto")

    def detect(self, image_path: str | Path) -> Optional[BallBox]:
        kwargs: dict[str, Any] = {"conf": self.conf, "iou": self.iou, "imgsz": self.imgsz}
        if self.half:
            kwargs["half"] = True
        result = self.model(str(image_path), **kwargs)
        res = result[0] if isinstance(result, (list, tuple)) else result
        boxes = getattr(res, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return None

        best: Optional[BallBox] = None
        for i in range(len(boxes)):
            if int(boxes.cls[i].item()) != self.ball_class_id:
                continue
            conf = float(boxes.conf[i].item())
            cx, cy, w, h = (float(v) for v in boxes.xywhn[i].tolist())
            if not all(math.isfinite(v) for v in (cx, cy, w, h, conf)):
                continue
            cx = min(max(cx, 0.0), 1.0)
            cy = min(max(cy, 0.0), 1.0)
            w = min(max(w, 0.0), 1.0)
            h = min(max(h, 0.0), 1.0)
            if w <= 0.0 or h <= 0.0:
                continue
            if best is None or conf > best[4]:
                best = (cx, cy, w, h, conf)
        return best


def resolve_model(db, model_name_or_version: str | None = None) -> Model | None:
    """Resolve a model by name/version, else the active model, else the newest."""
    if model_name_or_version:
        m = db.execute(
            select(Model).where(Model.name == model_name_or_version)
        ).scalar_one_or_none()
        if m is None and str(model_name_or_version).isdigit():
            m = db.execute(
                select(Model).where(Model.version == int(model_name_or_version))
            ).scalar_one_or_none()
        return m
    m = db.execute(select(Model).where(Model.is_active.is_(True))).scalar_one_or_none()
    if m is None:
        m = db.execute(select(Model).order_by(Model.version.desc())).scalars().first()
    return m


def build_detector(model: Model | None, settings: dict) -> Optional[BallDetector]:
    """Construct a detector for ``model``.

    Returns ``None`` only when there is no model or no loadable weights. A model
    whose weights exist but fail to load raises (callers decide how to handle).
    """
    weights = model_weights_ref(model)
    if model is None or weights is None:
        return None
    # Bootstrap COCO weights emit the ball as the configured COCO class id;
    # models fine-tuned on our single-class dataset emit class 0.
    ball_class_id = int(settings.get("model.ball_class_id", 32)) if model.is_bootstrap else 0
    return BallDetector(
        weights,
        ball_class_id=ball_class_id,
        conf=float(settings.get("model.inference.conf_threshold", 0.25)),
        iou=float(settings.get("model.inference.iou_threshold", 0.45)),
        imgsz=int(settings.get("model.inference.imgsz", 640)),
        device=str(settings.get("model.inference.device", "auto") or "auto"),
        half=bool(settings.get("model.inference.half", False)),
    )


def load_active_detector(
    db, settings: dict, model_name_or_version: str | None = None
) -> tuple[Optional[BallDetector], Model | None]:
    """Resolve a model and build its detector. Returns (detector, model)."""
    model = resolve_model(db, model_name_or_version)
    return build_detector(model, settings), model
