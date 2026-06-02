# Configuration

Everything tunable lives in **one config file** loaded by `pb2core.config` and
shared by the web app, worker, and CLI. The default format is YAML
(`config.yaml`); every key may be overridden by an environment variable so the
container can be configured without editing files (see
[deployment.md](./deployment.md)).

Resolution order (highest priority last): built-in defaults → config file →
environment variables → CLI flags (CLI only).

## Example `config.yaml`

```yaml
storage:
  root: /data                # the volume mount; all files live under here

database:
  url: sqlite:////data/db.sqlite3
  # url: ******db:5432/pb2

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
  # The "current" model the app uses for pre-labeling. May be overridden by the
  # models table / models/current symlink; config provides the bootstrap value.
  current_path: /data/models/current
  bootstrap_weights: yolov8n.pt   # used on first run if no model exists
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
  base_weights: yolov8n.pt   # what to fine-tune from
  epochs: 100
  imgsz: 640
  batch: 16
  val_split: 0.15            # fraction of labeled frames held out for validation
  device: auto
  project_dir: /data/datasets

server:
  host: 0.0.0.0
  port: 8000
  cors_origins: ["*"]
```

## Key reference

### `storage`
- **`root`** — absolute path that everything is written under and the Docker
  volume mount point. See [storage.md](./storage.md).

### `database`
- **`url`** — SQLAlchemy connection string. SQLite default; Postgres supported.

### `ingestion`
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

### `model`
- **`current_path`** — where the app loads the active YOLO weights from. The
  CLI's `set-current` updates the `models/current` symlink this points at.
- **`bootstrap_weights`** — model used on a fresh install before any training
  run exists (e.g. stock COCO YOLO that knows class 32).
- **`ball_class_id`** — the COCO id treated as "ball" during pre-labeling
  (`32` = sports ball). Frames where this class is detected go to the
  **validation** queue; otherwise **training**. See
  [ingestion-pipeline.md](./ingestion-pipeline.md).
- **`inference.*`** — confidence/IoU thresholds, image size, device.

### `labeling`
- **`max_undo_steps`** — the configurable maximum undo depth required by the
  problem statement for Tabs 2 & 3.
- **`box_min_size_px`** — drags smaller than this are treated as "no box".
- **`autosave`** — write the label as soon as the user advances ("D").

### `ui`
- **`pagination_size`** — videos shown per page in Tab 1's list.
- **`poll_interval_ms`** — fallback polling cadence for job status if WebSockets
  are unavailable.

### `training` (CLI)
- **`base_weights`** — starting weights to fine-tune.
- **`epochs` / `imgsz` / `batch` / `device`** — standard YOLO training knobs.
- **`val_split`** — fraction of human-labeled frames reserved for the training
  run's validation split (distinct from the app's "validation queue").

### `server`
- **`host` / `port` / `cors_origins`** — API server binding and CORS.

## Why a single config file matters

Because `pb2core.config` is imported by all entrypoints, the CLI and the web app
always agree on the model path, the ball class id, the storage root, and the DB
location. There is no second place to keep these in sync. The model path being
config-driven is what makes **hot-swapping a freshly trained model** a one-line
change (see [cli.md](./cli.md)).
