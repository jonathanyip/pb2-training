# Architecture

## Component diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Docker container(s)                          │
│                                                                        │
│  ┌────────────────────────┐         ┌──────────────────────────────┐  │
│  │  Frontend (SPA)        │  HTTP   │  Backend API (FastAPI)        │  │
│  │  3 tabs: Upload /      │◀───────▶│  - REST endpoints (see api.md)│  │
│  │  Train / Validate      │  +WS    │  - serves frame images        │  │
│  │  canvas bbox + WASD    │         │  - enqueues ingestion jobs    │  │
│  └────────────────────────┘         └──────────────┬───────────────┘  │
│                                                     │ imports          │
│  ┌────────────────────────┐                        ▼                  │
│  │  Worker (background)    │         ┌──────────────────────────────┐  │
│  │  - yt_dlp download      │────────▶│  pb2core (shared library)     │  │
│  │  - frame sampling       │ imports │  - db models / migrations     │  │
│  │  - YOLO pre-labeling     │         │  - storage layout helpers    │  │
│  └────────────────────────┘         │  - YOLO wrapper (infer/label) │  │
│                                      │  - dataset export             │  │
│  ┌────────────────────────┐ imports  └──────────────┬───────────────┘  │
│  │  CLI (pb2 ...)         │─────────────────────────┘                  │
│  │  train / set-current / │                                            │
│  │  reanalyze / export    │                                            │
│  └────────────────────────┘                                            │
└───────────────┬─────────────────────────────┬────────────────────────┘
                │                              │
        ┌───────▼────────┐            ┌────────▼─────────┐
        │ DB (SQLite or  │            │ Storage volume   │
        │ Postgres)      │            │ videos/ frames/  │
        └────────────────┘            │ models/ datasets/│
                                      └──────────────────┘
```

## The shared library: `pb2core`

The single most important architectural decision: **the web app, the background
worker, and the CLI all depend on one library, `pb2core`.** The problem
statement calls this out explicitly ("the CLI tool also needs to know how the
database is structured, potentially it should share library code?"). Yes — it
shares all of it.

`pb2core` owns:

- **Database models & migrations** — the schema in
  [data-model.md](./data-model.md). Defined once; both web and CLI open the same
  DB through the same ORM models.
- **Storage layout** — functions that compute paths for a video or a frame so
  there is exactly one source of truth for where files live (see
  [storage.md](./storage.md)).
- **Config loading** — parses and validates the config file
  ([configuration.md](./configuration.md)).
- **YOLO wrapper** — load the current model, run inference on a frame, extract
  ball (class 32) boxes, and convert to/from YOLO label format.
- **Ingestion primitives** — download (yt_dlp), sample frames, pre-label.
- **Dataset export** — turn DB labels into a YOLO-format dataset on disk.

```
pb2core/
├── config.py          # load + validate config
├── db/
│   ├── models.py      # ORM models (Video, Frame, Label, Model, ...)
│   ├── session.py     # engine/session factory
│   └── migrations/    # schema migrations
├── storage.py         # path computation, collision handling
├── yolo.py            # model load, inference, label conversion
├── ingest.py          # download, sample, pre-label
├── dataset.py         # export DB -> YOLO dataset
└── reanalyze.py       # re-run model over training frames
```

## Tech stack

These are recommendations; the design does not hard-depend on exact choices.

| Concern | Choice | Why |
|---------|--------|-----|
| Language | Python 3.11+ | YOLO/ML ecosystem, yt_dlp, LibreYOLO are Python |
| Backend API | FastAPI + Uvicorn | async, typed, auto OpenAPI for [api.md](./api.md) |
| Background jobs | Worker process + DB-backed job table (optionally RQ/Celery + Redis) | ingestion is long-running; must not block requests |
| ORM / migrations | SQLAlchemy + Alembic | shared models across app & CLI |
| Database | SQLite by default, Postgres optional | SQLite is zero-config for single-node; Postgres for scale |
| Frontend | SPA (React or Svelte) | canvas drawing + global WASD key handling |
| Video download | `yt_dlp` | robust YouTube downloading |
| Frame sampling | `ffmpeg` (via `ffmpeg-python`) or OpenCV | reliable, fast decoding |
| Inference | Ultralytics YOLO and/or LibreYOLO | pre-labeling |
| Training | [LibreYOLO](https://github.com/LibreYOLO/libreyolo) | reads YOLO-format datasets; MIT-licensed |
| CLI | `pb2` (Typer/Click) | thin wrapper over `pb2core` |
| Packaging | Docker + docker-compose | see [deployment.md](./deployment.md) |

## Process topology

Three logical processes, all sharing the DB and storage volume:

1. **API server** — serves the SPA and REST/WS endpoints; fast, never blocks on
   heavy work.
2. **Worker** — drains the ingestion job queue (download → sample → pre-label).
   Can be scaled horizontally; GPU-affinity recommended for inference.
3. **CLI** — invoked on demand by an operator (training, model swap, reanalyze).
   Runs in the same image so it has `pb2core` and the storage volume mounted.

For a minimal single-node deployment the API and worker can run in the same
container via a process manager, but they remain logically separate so they can
be split later. See [deployment.md](./deployment.md).

## Why a job queue for ingestion?

Downloading a YouTube video and running YOLO over thousands of sampled frames
takes minutes. The upload request must return immediately with a job id; the
worker processes the job and updates status, which Tab 1 polls (or receives via
WebSocket). See [ingestion-pipeline.md](./ingestion-pipeline.md).
