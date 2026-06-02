# pb2-training — Design Documentation

`pb2-training` is a Dockerized web application that helps you build and
progressively train a [YOLO](https://docs.ultralytics.com/) model that detects
the **ball** as it flies around the court in a game of pickleball.

The app turns raw match footage (YouTube links or local uploads) into a curated,
human-verified YOLO training dataset through a tight loop of:

1. **Upload & ingest** videos, then sample frames and pre-label them with an
   existing YOLO model.
2. **Train** — humans draw the ball's bounding box on frames the model *missed*.
3. **Validate** — humans approve/correct the boxes the model *found*.
4. **Re-train** the model from the curated data using a CLI tool, swap in the new
   weights, and re-analyze the backlog so the model keeps improving.

## How to read these docs

The design is intentionally split into focused documents. Start at the top and
drill down as needed.

| # | Document | What it covers |
|---|----------|----------------|
| 1 | [overview.md](./overview.md) | Goals, glossary, end-to-end workflow, the "progressive training" loop |
| 2 | [architecture.md](./architecture.md) | Components, tech stack, runtime topology, shared library |
| 3 | [storage.md](./storage.md) | On-disk layout for videos and frames, naming/collision handling |
| 4 | [data-model.md](./data-model.md) | Database schema, entities, state machine for frames |
| 5 | [configuration.md](./configuration.md) | The config file, every tunable, defaults |
| 6 | [ingestion-pipeline.md](./ingestion-pipeline.md) | Download/upload, frame sampling, YOLO pre-labeling, job queue |
| 7 | [ui-upload.md](./ui-upload.md) | Tab 1 — Upload UX & paginated video list |
| 8 | [ui-training.md](./ui-training.md) | Tab 2 — Train UX, bounding-box drawing, WASD hotkeys |
| 9 | [ui-validation.md](./ui-validation.md) | Tab 3 — Validate UX, pre-populated boxes |
| 10 | [api.md](./api.md) | Backend HTTP API reference |
| 11 | [cli.md](./cli.md) | CLI tool: training, model upgrade, re-analysis |
| 12 | [deployment.md](./deployment.md) | Docker / docker-compose, volumes, env vars |

## Design principles

- **Everything configurable lives in one config file** (see
  [configuration.md](./configuration.md)). Frame sample rate, the YOLO model
  path, undo depth, pagination size, and storage root are all config-driven.
- **One shared library, two entrypoints.** The web app and the CLI tool both
  import the same `pb2core` package so the database schema, storage layout, and
  YOLO logic are defined exactly once. See [architecture.md](./architecture.md).
- **Every frame has a stable UUID.** Frames are tracked by UUID end-to-end so
  labels, files, and database rows never drift apart. See
  [data-model.md](./data-model.md).
- **The model is a hot-swappable artifact.** The app always loads "the current
  model" by path; the CLI produces new versioned weights that become current.
  See [cli.md](./cli.md).
