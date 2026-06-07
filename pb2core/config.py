from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from pb2core.defaults import DEFAULT_RUNTIME_SETTINGS


CONFIG_PATH = Path(os.getenv("PB2_CONFIG", "config.yaml"))


def _load_yaml() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def normalize_base_path(value: str | None) -> str:
    """Normalize a URL base path.

    Returns "" for root hosting, or "/segment[/segment...]" with a leading
    slash and no trailing slash (e.g. "pb2-training/" -> "/pb2-training").
    """
    p = (value or "").strip()
    if not p or p == "/":
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")


def get_bootstrap_config() -> dict[str, Any]:
    y = _load_yaml()
    storage_root = y.get("storage", {}).get("root", "./data")
    db_url = y.get("database", {}).get("url", "sqlite:///./data/db.sqlite3")
    server = y.get("server", {})
    # Base path lets the app be hosted under a sub-path behind a reverse proxy
    # (e.g. https://example.com/pb2-training). The PB2_BASE_PATH env var wins,
    # which is convenient for docker-compose integrations.
    base_path = normalize_base_path(os.getenv("PB2_BASE_PATH", server.get("base_path", "")))
    return {
        "storage": {"root": storage_root},
        "database": {"url": db_url},
        "server": {
            "host": server.get("host", "0.0.0.0"),
            "port": server.get("port", 8000),
            "cors_origins": server.get("cors_origins", ["*"]),
            "base_path": base_path,
        },
    }


def seed_settings_if_empty(db) -> None:
    from pb2core.db.models import Setting

    existing = db.execute(select(Setting.key)).first()
    if existing:
        return

    yaml_cfg = _load_yaml()

    def flatten(prefix: str, value: Any, out: dict[str, Any]):
        if isinstance(value, dict):
            for k, v in value.items():
                flatten(f"{prefix}.{k}" if prefix else k, v, out)
        else:
            out[prefix] = value

    yaml_flat: dict[str, Any] = {}
    flatten("", yaml_cfg, yaml_flat)

    merged = dict(DEFAULT_RUNTIME_SETTINGS)
    for k, v in yaml_flat.items():
        if k in merged:
            merged[k] = v

    for key, value in merged.items():
        db.add(Setting(key=key, value=value))
    db.commit()


def get_runtime_settings(db) -> dict[str, Any]:
    from pb2core.db.models import Setting

    seed_settings_if_empty(db)
    rows = db.execute(select(Setting)).scalars().all()
    vals = dict(DEFAULT_RUNTIME_SETTINGS)
    for row in rows:
        vals[row.key] = row.value
    return vals
