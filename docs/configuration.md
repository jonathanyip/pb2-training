# Configuration

Configuration is split into two layers:

- **Bootstrap settings** — where the database and storage live, and the server
  binding. These must be known *before* the DB is open, so they come from a
  small YAML file / environment variables. This is the only thing that has to be
  set at deploy time.
- **Runtime settings** — everything an operator tunes day-to-day (sampling rate,
  inference thresholds, undo depth, pagination, training defaults, and the
  **active model**). These are stored in the database (`settings` table — see
  [data-model.md](./data-model.md)) and edited live from the **Settings tab**
  (Tab 4 — see [ui-settings.md](./ui-settings.md)). No redeploy or file edit
  needed.

`pb2core.config` exposes both layers behind one accessor so the web app, worker,
and CLI all read the same effective settings.

Resolution order for a runtime key (highest priority last): built-in defaults →
YAML file values (used to **seed** the DB on first boot) → DB `settings` row →
CLI flag (CLI invocation only). Once the DB is seeded, the DB row is the source
of truth and the Settings tab is the way to change it; editing the YAML file
afterward does **not** override an existing DB value.

## Bootstrap file (`config.yaml`)

Only the keys needed to open the database and storage and bind the server:

```yaml
storage:
  root: /data                # the volume mount; all files live under here

database:
  url: sqlite:////data/db.sqlite3
  # url: ******db:5432/pb2

server:
  host: 0.0.0.0
  port: 8000
  cors_origins: ["*"]
```

## Runtime settings (DB-backed, edited in the Settings tab)

On first boot, if the `settings` table is empty, it is seeded from the defaults
below (which may also be supplied in the YAML file for convenience). Afterward
they live in the DB and are changed from Tab 4.

## Example seed / default values

```yaml
ingestion:
  keep_source_video: true    # delete source.mp4 after sampling if false
  youtube:
    format: "bestvideo[ext=mp4]/best"  # passed to yt_dlp
    rate_limit: null         # e.g. "5M" to throttle
  max_concurrent_jobs: 2

sampling:
  mode: every_n_frames       # every_n_frames | fps | interval_seconds
  every_n_frames: 15         # used when mode=every_n_frames
  target_fps: 2              # used when mode=fps
  interval_seconds: 0.5      # used when mode=interval_seconds
  image_format: jpg
  jpeg_quality: 90
  max_frames_per_video: null # cap, null = unlimited

model:
  # Which model is used for post-analysis (pre-labeling) is NOT set here — it is
  # the row with models.is_active=true, chosen in the Settings tab or via the CLI
  # `set-active`. See data-model.md and ui-settings.md.
  bootstrap_weights: yolov8n.pt   # external weights used to seed the first
                                  # (bootstrap) model on a fresh install
  ball_class_id: 32          # COCO "sports ball"; export remaps to class 0
  inference:
    conf_threshold: 0.25
    iou_threshold: 0.45
    imgsz: 640
    device: auto             # auto | cpu | cuda:0
    half: false

labeling:
  max_undo_steps: 20         # WASD "A" undo depth (Tabs 2 & 3)
  box_min_size_px: 4         # ignore accidental tiny drags
  autosave: true

ui:
  pagination_size: 24        # videos per page in Tab 1
  poll_interval_ms: 2000     # ingestion status polling fallback

training:                    # used by the CLI `pb2 train`
  base_weights: yolov8n.pt   # what to fine-tune from when there is no parent model
  epochs: 100
  imgsz: 640
  batch: 16
  val_split: 0.15            # fraction of labeled frames held out for validation
  device: auto
  project_dir: /data/datasets
```

## Key reference

> **bootstrap** = lives in `config.yaml` (needed before the DB opens).
> **runtime** = lives in the `settings` table, edited in the Settings tab.

### `storage` (bootstrap)
- **`root`** — absolute path that everything is written under and the Docker
  volume mount point. See [storage.md](./storage.md).

### `database` (bootstrap)
- **`url`** — SQLAlchemy connection string. SQLite default; Postgres supported.

### `server` (bootstrap)
- **`host` / `port` / `cors_origins`** — API server binding and CORS.

### `ingestion` (runtime)
- **`keep_source_video`** — keep or delete `source.<container>` after sampling.
- **`youtube.format`** / **`youtube.rate_limit`** — passed through to `yt_dlp`.
- **`max_concurrent_jobs`** — worker concurrency for downloads/labeling.

### `sampling`
- **`mode`** — how frames are chosen: every Nth decoded frame, a target fps, or
  a fixed time interval.
- **`every_n_frames` / `target_fps` / `interval_seconds`** — parameter for the
  chosen mode.
- **`image_format` / `jpeg_quality`** — on-disk frame encoding.
- **`max_frames_per_video`** — optional cap to bound dataset growth/disk.

### `model` (runtime)
- **Active model** — *which* model post-analysis uses is **not** a config key; it
  is the `models.is_active` row, selected in the Settings tab or via
  `pb2 set-active`. See [data-model.md](./data-model.md) and
  [ui-settings.md](./ui-settings.md).
- **`bootstrap_weights`** — external weights used to seed the **first** model on a
  fresh install (e.g. stock COCO YOLO that knows class 32). See `pb2 bootstrap`
  in [cli.md](./cli.md).
- **`ball_class_id`** — the COCO id treated as "ball" during pre-labeling
  (`32` = sports ball). Frames where this class is detected go to the
  **validation** queue; otherwise **training**. See
  [ingestion-pipeline.md](./ingestion-pipeline.md).
- **`inference.*`** — confidence/IoU thresholds, image size, device.

### `labeling` (runtime)
- **`max_undo_steps`** — the configurable maximum undo depth required by the
  problem statement for Tabs 2 & 3.
- **`box_min_size_px`** — drags smaller than this are treated as "no box".
- **`autosave`** — write the label as soon as the user advances ("D").

### `ui` (runtime)
- **`pagination_size`** — videos shown per page in Tab 1's list.
- **`poll_interval_ms`** — fallback polling cadence for job status if WebSockets
  are unavailable.

### `training` (runtime / CLI)
- **`base_weights`** — starting weights to fine-tune **when training a model that
  has no parent** (e.g. the first trained model). When generating `V2` from `V1`,
  the parent's weights are used instead (see [cli.md](./cli.md)).
- **`epochs` / `imgsz` / `batch` / `device`** — standard YOLO training knobs.
- **`val_split`** — fraction of human-labeled frames reserved for the training
  run's validation split (distinct from the app's "validation queue").

## Why DB-backed config matters

Because `pb2core.config` reads the same `settings` table for every entrypoint,
the CLI and the web app always agree on the ball class id, sampling rate, and
which model is active — and an operator can change any of it from the Settings
tab without touching files or redeploying. The bootstrap YAML stays tiny and
stable (just where the DB and storage live), while the things people actually
tune live in the DB where the UI can edit them and changes are auditable
(`settings.updated_at` / `updated_by`).
