from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import func, select

from pb2core.dataset import export_dataset
from pb2core.db.models import Frame, Model, ModelTrainedFrame
from pb2core.db.session import SessionLocal
from pb2core.init_db import init_db
from pb2core.reanalyze import reanalyze_training_backlog
from pb2core.storage import storage
from pb2core.training import train_model

app = typer.Typer(help="pb2 CLI")
models_app = typer.Typer(help="Model commands")
db_app = typer.Typer(help="DB commands")
app.add_typer(models_app, name="models")
app.add_typer(db_app, name="db")


@db_app.command("migrate")
def migrate() -> None:
    init_db()
    typer.echo("migrations applied")


@app.command()
def bootstrap(name: str = "bootstrap", weights: str = "yolov8n.pt") -> None:
    init_db()
    with SessionLocal() as db:
        exists = db.execute(select(Model).where(Model.name == name)).scalar_one_or_none()
        if exists:
            typer.echo(f"model already exists: {name}")
            raise typer.Exit(1)
        max_version = db.execute(select(Model.version).order_by(Model.version.desc())).scalars().first()
        version = 0 if max_version is None else max_version + 1
        path = storage.model_path(version)
        storage.absolute(path).write_text(f"bootstrap weights: {weights}\n", encoding="utf-8")
        m = Model(name=name, version=version, path=str(path), is_bootstrap=True, base_weights=weights)
        if db.execute(select(Model).where(Model.is_active.is_(True))).scalar_one_or_none() is None:
            m.is_active = True
        db.add(m)
        db.commit()
        typer.echo(f"bootstrapped {name} v{version:04d}")


@app.command("export")
def export_cmd(out_id: str = "auto") -> None:
    init_db()
    with SessionLocal() as db:
        export_id = export_dataset(db)
    typer.echo(f"datasets/{export_id}")


@app.command()
def train(name: str, from_: str = typer.Option(None, "--from"), full: bool = False) -> None:
    init_db()
    with SessionLocal() as db:
        m = train_model(db, name=name, parent_name_or_version=from_, full=full)
        typer.echo(f"trained {m.name} v{m.version:04d}")


@models_app.command("ls")
def models_ls() -> None:
    init_db()
    with SessionLocal() as db:
        rows = db.execute(select(Model).order_by(Model.version.asc())).scalars().all()
        for m in rows:
            flag = "*" if m.is_active else " "
            trained = db.execute(select(func.count()).select_from(ModelTrainedFrame).where(ModelTrainedFrame.model_id == m.id)).scalar_one()
            typer.echo(f"{flag} v{m.version:04d} {m.name} trained_frames={trained} base={m.base_model_id or '-'}")


@app.command("set-active")
def set_active(model: str) -> None:
    init_db()
    with SessionLocal() as db:
        target = db.execute(select(Model).where(Model.name == model)).scalar_one_or_none()
        if target is None and model.isdigit():
            target = db.execute(select(Model).where(Model.version == int(model))).scalar_one_or_none()
        if target is None:
            typer.echo("model not found")
            raise typer.Exit(1)
        for m in db.execute(select(Model)).scalars().all():
            m.is_active = m.id == target.id
        db.commit()
        typer.echo(f"active={target.name}")


@app.command()
def reanalyze(model: str | None = None, dry_run: bool = False) -> None:
    init_db()
    with SessionLocal() as db:
        moved = reanalyze_training_backlog(db, model_name_or_version=model, dry_run=dry_run)
        typer.echo(json.dumps({"moved": moved, "dry_run": dry_run}))


@app.command()
def stats() -> None:
    init_db()
    with SessionLocal() as db:
        train_un = db.execute(select(func.count()).select_from(Frame).where(Frame.queue == "training", Frame.status == "unprocessed")).scalar_one()
        val_un = db.execute(select(func.count()).select_from(Frame).where(Frame.queue == "validation", Frame.status == "unprocessed")).scalar_one()
        processed = db.execute(select(func.count()).select_from(Frame).where(Frame.status == "processed")).scalar_one()
        typer.echo(json.dumps({"training_unprocessed": train_un, "validation_unprocessed": val_un, "processed": processed}))


if __name__ == "__main__":
    app()
