# Storage layout

All persistent files live under a single **storage root** provided by the user
(config key `storage.root`, also the Docker volume mount point). The app never
writes outside this root. The database stores **paths relative to the root** so
the volume can be moved or re-mounted without breaking references.

```
<storage.root>/
├── videos/
│   └── <video_uuid>/
│       ├── source.mp4            # the original/downloaded video
│       └── meta.json             # denormalized copy of DB metadata (optional)
├── frames/
│   └── <video_uuid>/
│       ├── <frame_uuid>.jpg      # one sampled frame
│       ├── <frame_uuid>.jpg
│       └── ...
├── models/
│   ├── current -> v0003.pt       # symlink to the active model (or DB pointer)
│   ├── v0001.pt
│   ├── v0002.pt
│   └── v0003.pt
├── datasets/
│   └── <export_uuid>/            # YOLO-format dataset snapshots for training
│       ├── data.yaml
│       ├── images/{train,val}/
│       └── labels/{train,val}/
└── db.sqlite3                    # when using SQLite (Postgres lives elsewhere)
```

## Why UUID directories?

The problem statement requires handling **videos with overlapping names** and a
**stable unique id per frame**. UUIDs solve both:

- **Videos:** Two different uploads both named `game.mp4` would collide if we
  used human names as paths. Instead each video gets a `video_uuid` (UUID4) and
  its files live under `videos/<video_uuid>/`. The human-friendly title is kept
  **only in the database** (`videos.title`, `videos.original_filename`,
  `videos.source_url`) and shown in the UI; it never affects the path.
- **Frames:** Each sampled frame gets its own `frame_uuid`. The image file is
  `frames/<video_uuid>/<frame_uuid>.jpg`. This UUID is the key used everywhere —
  in labels, in the review queues, and in exported YOLO label filenames — so a
  frame can always be traced back to its file and its source video.

The original `source.mp4` keeps a fixed name *inside* its UUID directory, so we
never need to sanitize or de-duplicate user-supplied filenames at all.

## Naming & collision handling rules

1. **Never derive a directory name from user input.** Directories are UUIDs.
2. **Store the display name in the DB**, not the filesystem. Duplicate titles
   are perfectly fine; they are different rows with different UUIDs.
3. **Source file extension** is normalized/known from the download or upload
   (e.g. always remux to `.mp4`, or keep the real container and record it in
   `videos.container`). The on-disk basename is always `source`.
4. **Frame filenames** are `<frame_uuid>.jpg` (format/quality from config —
   `sampling.image_format`, `sampling.jpeg_quality`).

## Frame ↔ label file relationship (for export)

The database is the source of truth for labels. When exporting a YOLO dataset
(see [cli.md](./cli.md)), `pb2core.dataset` copies/symlinks the frame image and
writes a sibling `.txt` label file **named by the same `frame_uuid`** so YOLO's
image/label pairing works:

```
datasets/<export_uuid>/images/train/<frame_uuid>.jpg
datasets/<export_uuid>/labels/train/<frame_uuid>.txt   # "0 cx cy w h" (normalized)
```

Empty `.txt` files represent negative samples (frames a human confirmed have no
ball) — these are valuable hard negatives for training.

## Path helpers live in `pb2core.storage`

To keep the layout consistent across the web app, worker, and CLI, **no code
hand-builds paths**. `pb2core.storage` exposes helpers such as:

- `video_dir(video_uuid)` → `videos/<video_uuid>/`
- `video_source_path(video_uuid, container)` 
- `frame_dir(video_uuid)`
- `frame_path(video_uuid, frame_uuid)`
- `models_dir()`, `current_model_path()`
- `dataset_dir(export_uuid)`

All return paths relative to `storage.root`; an `absolute()` helper joins them
to the configured root. This is the single source of truth referenced by
[data-model.md](./data-model.md) and [architecture.md](./architecture.md).

## Disk-space considerations

- Sampled frames dominate disk usage. Sample rate is configurable
  (`sampling.*`) so operators trade dataset size vs. disk.
- Original videos can optionally be deleted after successful sampling
  (`ingestion.keep_source_video: false`) to reclaim space; the DB keeps the
  source URL so a video could be re-downloaded if needed.
- Old model versions are retained in `models/` for rollback; pruning is manual.
