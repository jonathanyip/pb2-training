from sqlalchemy import select

from pb2core.config import get_runtime_settings, seed_settings_if_empty
from pb2core.db.base import Base
from pb2core.db.models import Model
from pb2core.db.session import ENGINE, SessionLocal
from pb2core.storage import storage


def init_db() -> None:
    storage.root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=ENGINE)
    with SessionLocal() as db:
        seed_settings_if_empty(db)
        get_runtime_settings(db)
        active = db.execute(select(Model).where(Model.is_active.is_(True))).scalar_one_or_none()
        if active is None:
            m = db.execute(select(Model).where(Model.version == 0)).scalar_one_or_none()
            if m is None:
                path = storage.model_path(0)
                abs_path = storage.absolute(path)
                abs_path.write_text("bootstrap model placeholder\n", encoding="utf-8")
                m = Model(
                    name="bootstrap",
                    version=0,
                    path=str(path),
                    is_active=True,
                    is_bootstrap=True,
                    base_weights="yolov8n.pt",
                )
                db.add(m)
                db.commit()
                return
            m.is_active = True
            db.commit()
