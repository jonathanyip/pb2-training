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

## Local development & testing

The heavy lifting uses [LibreYOLO](https://pypi.org/project/libreyolo/) (inference
+ training), [yt-dlp](https://github.com/yt-dlp/yt-dlp) (YouTube download) and
**ffmpeg/ffprobe** (probing + frame sampling), so `ffmpeg` must be on your `PATH`
(`brew install ffmpeg` / `apt-get install ffmpeg`).

### 1. Install with [uv](https://docs.astral.sh/uv/)

```bash
uv sync   # creates .venv and installs the project + deps (incl. torch) from uv.lock
```

`uv sync` reads `pyproject.toml`/`uv.lock` and installs everything (yt-dlp and
libreyolo are already declared as dependencies). Prefix the commands below with
`uv run`, which uses the project environment automatically — no manual activation
needed. (Plain `python -m venv .venv && .venv/bin/pip install -e .` also works if
you prefer pip.)

### 2. Bootstrap a first model

```bash
export PB2_CONFIG=config.yaml
uv run pb2 bootstrap --name YOLOX_V1 --weights LibreYOLO9t.pt
```

This downloads `LibreYOLO9t.pt` (~8 MB) into `weights/` on first use (needs
network) and marks the model active so ingestion can pre-label frames.

### 3. Run the app

```bash
# API + background worker in one process:
uv run uvicorn pb2app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and use the **Upload** tab to add a short YouTube URL
or drag-drop a video. The worker downloads → probes → samples → pre-labels each
frame, routing it to the **Train** (model missed the ball) or **Validate** (model
found a ball) tab. Label a few frames there, then continue with the CLI loop.

### 4. Train a model (the progressive loop)

Each iteration trains a **new** model on only the frames labeled since its parent,
activates it, then re-routes the backlog so the next round needs less manual work.

**Step 1 — Seed the first model** (once, on a fresh install). This is the `bootstrap`
step from §2; it gives the worker something to pre-label with. Make sure it's the
active model:

```bash
export PB2_CONFIG=config.yaml
uv run pb2 models ls          # the active model is marked with "*"
uv run pb2 set-active YOLOv9_V1   # if it isn't already active
```

> Note: `bootstrap` only auto-activates its model when **no** model is active yet.
> If you bootstrap into an existing database, set it active explicitly.

**Step 2 — Ingest footage.** In the **Upload** tab add YouTube URLs or upload
videos. The worker samples frames and pre-labels them with the active model.

**Step 3 — Label frames.** Clear the queues in the UI:
- **Train tab** — draw boxes on frames the model missed (or mark "no ball").
- **Validate tab** — approve/adjust boxes the model already found.

Only **processed** (human-reviewed) frames are used for training. Check progress:

```bash
uv run pb2 stats   # {"training_unprocessed":N, "validation_unprocessed":N, "processed":N}
```

**Step 4 — Train a new model.** Fine-tunes from the parent's weights, training only
on frames the parent (and its ancestors) never saw. Needs ≥2 processed frames:

```bash
uv run pb2 train --name YOLOv9_V2 --from YOLOv9_V1   # incremental from the parent
# uv run pb2 train --name YOLOv9_V2 --from YOLOv9_V1 --full   # retrain on ALL processed frames
```

Best weights land in `data/models/vNNNN.pt`; the model is registered but **not yet
active**. Inspect lineage and metrics:

```bash
uv run pb2 models ls
```

**Step 5 — Activate it.**

```bash
uv run pb2 set-active YOLOv9_V2
```

**Step 6 — Re-analyze the backlog.** Re-run the new active model over the
unlabeled **training** frames; any frame it now finds a ball in is promoted to the
(cheaper) Validate queue. Verified frames are never touched.

```bash
uv run pb2 reanalyze --dry-run   # report how many would move, without writing
uv run pb2 reanalyze             # actually promote them
```

Then go back to Step 3 — each pass leaves a smaller, easier backlog.

Other useful commands:

```bash
uv run pb2 export        # build a YOLO dataset from verified labels (prints datasets/<uuid>)
uv run pb2 db migrate    # apply DB migrations (also run automatically on startup)
```

See [docs/cli.md](./docs/cli.md) for the full command reference.

### Notes

- **Fresh start:** state lives in `./data` (gitignored). Delete or move it to reset.
- **macOS:** keep `training.workers: 0` (the default) to avoid DataLoader crashes.
- **Speed:** real training on CPU/MPS is slow — lower `training.epochs` and
  `training.imgsz` in the **Settings** tab for a quick smoke test.
- **Docker:** for a containerized deployment with a persistent volume, see
  [Deploy with Docker](#deploy-with-docker) below.

## Deploy with Docker

The image bundles ffmpeg and runs the API together with the in-process ingest
worker (download → sample → pre-label) in a single container. All state — the
SQLite database, downloaded videos, sampled frames, trained models, and exported
datasets — lives under **`/data`**, which is backed by a Docker volume so it
survives image rebuilds and container restarts.

> The container uses [`config.docker.yaml`](./config.docker.yaml) (selected via
> `PB2_CONFIG`), which points `storage.root` and the SQLite URL at `/data`. The
> repo-root `config.yaml` (paths under `./data`) is only for local runs.

### Option A — docker compose (recommended)

```bash
docker compose up --build -d        # build image + start in the background
docker compose logs -f api          # follow logs
```

Open <http://localhost:8000>. The named volume `pb2-data` holds everything in
`/data`. Stop without losing data:

```bash
docker compose down                 # stop; the pb2-data volume is kept
docker compose down -v              # stop AND delete the volume (wipes all state)
```

### Option B — plain docker run

Use a **named volume** (managed by Docker):

```bash
docker build -t pb2-training .
docker run -d --name pb2 -p 8000:8000 -v pb2-data:/data pb2-training
```

…or a **bind mount** if you want the data on your host filesystem (easy to
inspect and back up):

```bash
docker run -d --name pb2 -p 8000:8000 -v "$(pwd)/data:/data" pb2-training
```

### Bootstrap a model inside the container

A fresh volume has no active model, so ingestion routes every frame to the Train
queue until you seed one. Run `bootstrap` inside the running container (this
auto-downloads `LibreYOLO9t.pt`, so the container needs network access):

```bash
docker compose exec api pb2 bootstrap --name YOLOv9_V1 --weights LibreYOLO9t.pt
# plain docker run: docker exec pb2 pb2 bootstrap --name YOLOv9_V1 --weights LibreYOLO9t.pt
```

To bootstrap from a weights file you already have on the host, copy it into the
volume first, then point `--weights` at the in-container path:

```bash
docker compose cp ./LibreYOLO9m.pt api:/data/LibreYOLO9m.pt
docker compose exec api pb2 bootstrap --name YOLOv9_V1 --weights /data/LibreYOLO9m.pt
```

The same `pb2` subcommands from the training loop above (`train`, `set-active`,
`reanalyze`, `stats`, `models ls`) work via `docker compose exec api pb2 …`.

### Host under a sub-path (reverse proxy)

To serve the app under a sub-path such as `https://example.com/pb2-training`
(instead of the domain root), set **`PB2_BASE_PATH`** (or `server.base_path` in
the config). Every route, the static assets, the SPA, and the API docs are then
mounted under that prefix, and the frontend resolves all of its requests relative
to it.

```yaml
# Snippet for integrating into an existing docker-compose.yml
services:
  pb2-training:
    build: ./pb2-training          # or image: pb2-training
    environment:
      - PB2_BASE_PATH=/pb2-training
    volumes:
      - pb2-data:/data
    # no "ports:" needed if only the reverse proxy talks to it
    expose:
      - "8000"
    restart: unless-stopped

volumes:
  pb2-data:
```

This setup expects the proxy to **forward the full path unchanged** (i.e. the
container receives `/pb2-training/...`, the prefix is *not* stripped). Example
proxy configs:

```nginx
# nginx — pass the path through as-is
location /pb2-training/ {
    proxy_pass http://pb2-training:8000;   # note: no trailing slash -> no rewrite
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

```yaml
# Traefik labels (do NOT add a StripPrefix middleware)
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.pb2.rule=PathPrefix(`/pb2-training`)"
  - "traefik.http.services.pb2.loadbalancer.server.port=8000"
```

Then browse to `https://example.com/pb2-training/`. The app reaches its API at
`https://example.com/pb2-training/api/v1/...` and its docs at
`https://example.com/pb2-training/docs`. To serve at the root again, just unset
`PB2_BASE_PATH`.

### Back up / restore

Everything is under the volume, so backups are a single archive:

```bash
# Back up the named volume to ./pb2-backup.tar.gz
docker run --rm -v pb2-data:/data -v "$(pwd):/backup" alpine \
  tar czf /backup/pb2-backup.tar.gz -C /data .

# Restore into a (fresh) volume
docker run --rm -v pb2-data:/data -v "$(pwd):/backup" alpine \
  sh -c "cd /data && tar xzf /backup/pb2-backup.tar.gz"
```

> **GPU note:** the published image is CPU/MPS-oriented. For CUDA-accelerated
> training, run on an NVIDIA host with `--gpus all` and a CUDA-enabled PyTorch
> base image, and raise `training.workers` in the **Settings** tab.

