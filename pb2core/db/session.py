from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pb2core.config import get_bootstrap_config


def build_session_factory():
    cfg = get_bootstrap_config()
    connect_args = {"check_same_thread": False} if cfg["database"]["url"].startswith("sqlite") else {}
    engine = create_engine(cfg["database"]["url"], connect_args=connect_args)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


ENGINE, SessionLocal = build_session_factory()
