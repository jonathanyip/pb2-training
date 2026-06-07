from pb2core.config import seed_settings_if_empty
from pb2core.db.base import Base
from pb2core.db.session import ENGINE, SessionLocal
from pb2core.storage import storage


def init_db() -> None:
    storage.root.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=ENGINE)
    with SessionLocal() as db:
        seed_settings_if_empty(db)
    # A first model is created explicitly via `pb2 bootstrap`; until then there
    # is simply no active model and ingestion routes every frame to the training
    # queue. We intentionally do NOT auto-seed a placeholder model here, so the
    # operator's bootstrapped model is the one and only active model.
