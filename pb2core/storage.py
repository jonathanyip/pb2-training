from pathlib import Path

from pb2core.config import get_bootstrap_config


class Storage:
    def __init__(self):
        root = get_bootstrap_config()["storage"]["root"]
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for d in ["videos", "frames", "models", "datasets"]:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def absolute(self, rel: str | Path) -> Path:
        rel_path = Path(rel)
        if rel_path.is_absolute():
            raise ValueError("absolute paths are not allowed")
        p = (self.root / rel_path).resolve()
        p.relative_to(self.root)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def video_dir(self, video_uuid: str) -> Path:
        return Path("videos") / video_uuid

    def video_source_path(self, video_uuid: str, container: str) -> Path:
        return self.video_dir(video_uuid) / f"source.{container}"

    def frame_dir(self, video_uuid: str) -> Path:
        return Path("frames") / video_uuid

    def frame_path(self, video_uuid: str, frame_uuid: str, ext: str = "jpg") -> Path:
        return self.frame_dir(video_uuid) / f"{frame_uuid}.{ext}"

    def models_dir(self) -> Path:
        return Path("models")

    def model_path(self, version: int) -> Path:
        return self.models_dir() / f"v{version:04d}.pt"

    def dataset_dir(self, export_uuid: str) -> Path:
        return Path("datasets") / export_uuid


storage = Storage()
