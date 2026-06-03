# pb2-training

A Dockerized web application for building and **progressively training** a YOLO
model that detects the ball in pickleball footage.

It ingests match videos (YouTube or local upload), samples and pre-labels frames
with an existing YOLO model, then routes them to two keyboard-driven
human-review tabs — **Train** (draw boxes the model missed) and **Validate**
(approve boxes the model found). A CLI tool exports the verified labels, trains a
new model, hot-swaps it into the app, and re-analyzes the backlog so each
iteration needs less manual work.

## Design documentation

The full design lives in [`docs/`](./docs/README.md):

- [Overview](./docs/overview.md) · [Architecture](./docs/architecture.md)
- [Storage layout](./docs/storage.md) · [Data model](./docs/data-model.md) · [Configuration](./docs/configuration.md)
- [Ingestion pipeline](./docs/ingestion-pipeline.md)
- UI: [Upload](./docs/ui-upload.md) · [Train](./docs/ui-training.md) · [Validate](./docs/ui-validation.md) · [Settings](./docs/ui-settings.md)
- [API](./docs/api.md) · [CLI](./docs/cli.md) · [Deployment](./docs/deployment.md)

Start at [docs/README.md](./docs/README.md).
