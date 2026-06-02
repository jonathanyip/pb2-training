# Deployment

`pb2-training` ships as a Docker image and is intended to run via
`docker-compose`. The web app, the worker, and the CLI all live in the **same
image** (they share `pb2core`); compose runs the long-lived ones as services and
the CLI is invoked on demand with `docker compose run`.

## Image contents

A single image built from one `Dockerfile`:

- Python 3.11 + `pb2core`, the FastAPI app, the worker, and the `pb2` CLI.
- System deps: `ffmpeg` (sampling), and CUDA runtime if GPU inference/training is
  desired (a CPU-only variant works for inference too, just slower).
- The built frontend SPA, served as static files by the API (or by a sidecar
  nginx if preferred).

## Services

```yaml
# docker-compose.yml (illustrative)
services:
  api:
    image: pb2-training:latest
    command: uvicorn pb2app.main:app --host 0.0.0.0 --port 8000
    ports: ["8000:8000"]
    volumes:
      - pb2data:/data                 # storage.root  (videos, frames, models, db)
      - ./config.yaml:/data/config.yaml:ro
    environment:
      - PB2_CONFIG=/data/config.yaml
    depends_on: [db]                  # only if using Postgres

  worker:
    image: pb2-training:latest
    command: python -m pb2app.worker
    volumes:
      - pb2data:/data
      - ./config.yaml:/data/config.yaml:ro
    environment:
      - PB2_CONFIG=/data/config.yaml
    # deploy.resources.reservations.devices for GPU if available

  # Optional, only when database.url points at Postgres:
  db:
    image: postgres:16
    environment:
      - POSTGRES_PASSWORD=...        # use a secret, not committed
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pb2data:
  pgdata:
```

The default SQLite setup needs no `db` service — the database file lives on the
`pb2data` volume at `/data/db.sqlite3`.

## The storage volume = the configured path

The single `pb2data` volume mounted at `/data` **is** the "path in which they can
store the videos" from the problem statement. Everything in
[storage.md](./storage.md) (`videos/`, `frames/`, `models/`, `datasets/`, the
DB) lives here. Operators choose where this volume is backed (local disk, NFS,
cloud block storage) without the app caring, because every DB path is relative
to `storage.root`.

## Configuration & env overrides

- Mount `config.yaml` read-only into the container (see
  [configuration.md](./configuration.md)).
- Any key is overridable by an env var (e.g. `PB2_SAMPLING__EVERY_N_FRAMES=10`,
  `PB2_MODEL__BALL_CLASS_ID=32`) so deployments can be tuned without rebuilding.
- `PB2_CONFIG` points the process at the config file.

## Running the CLI

The CLI shares the image and the volume, so operations run against the same data:

```bash
docker compose run --rm worker pb2 stats
docker compose run --rm worker pb2 train --export auto
docker compose run --rm worker pb2 set-current 4
docker compose run --rm worker pb2 reanalyze
```

See [cli.md](./cli.md) for the full command set and the progressive-training
workflow.

## Migrations & startup

- On API/worker startup the app runs `pb2 db migrate` (Alembic) to ensure the
  schema is current before serving.
- On first boot, if no model exists, the app falls back to
  `model.bootstrap_weights` (e.g. stock COCO YOLO that recognizes class 32) so
  ingestion pre-labeling works before the first custom training run.

## GPU notes

- Inference (ingestion pre-labeling, reanalyze) and training benefit from a GPU.
  Set `model.inference.device` / `training.device` to `cuda:0` and grant the
  container GPU access (NVIDIA Container Toolkit).
- On a single GPU, keep `ingestion.max_concurrent_jobs` low and avoid running a
  heavy `pb2 train` at the same time as active ingestion to prevent VRAM
  contention.

## Scaling

- The **api** and **worker** are separate services and can be scaled
  independently. Multiple workers can drain the `ingest_jobs` queue in parallel
  (Postgres recommended over SQLite once more than one writer is involved).
- The frontend is static and can be fronted by a CDN/nginx if needed.

## Security & operational notes

- The core design assumes a trusted, single-team deployment (no built-in auth).
  To expose it more broadly, put it behind an authenticating reverse proxy or
  add a token middleware to the API (see [api.md](./api.md)).
- Database credentials and any secrets must come from Docker/compose secrets or
  environment, never committed to the repo or the config file.
- Back up the `pb2data` volume (it holds the DB, labels, and the model registry)
  — it is the entire state of the system.
