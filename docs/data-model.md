# Data model

The database is shared by the web app, the worker, and the CLI through the
`pb2core.db` models (see [architecture.md](./architecture.md)). SQLite is the
default; Postgres is supported by changing the connection string only.

All file references are stored as **paths relative to `storage.root`** (see
[storage.md](./storage.md)).

## Entity-relationship overview

```
videos (1) ───< frames (1) ───< labels
                  │
                  └── belongs to a queue (training | validation)
                  └── has a status   (unprocessed | processed)

models        — registry of YOLO weight files; one is "current"
ingest_jobs   — async work items for the worker
events        — optional audit log (undo history, model upgrades)
```

## Tables

### `videos`

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | `video_uuid`; directory name on disk |
| `source_type` | enum | `youtube` \| `upload` |
| `source_url` | text, null | YouTube URL (null for uploads) |
| `original_filename` | text, null | as uploaded (display only) |
| `title` | text | display name; duplicates allowed |
| `container` | text | e.g. `mp4`; used to build `source.<container>` |
| `duration_s` | float, null | from probe |
| `fps` | float, null | from probe |
| `status` | enum | `pending` \| `downloading` \| `sampling` \| `labeling` \| `ready` \| `failed` |
| `error` | text, null | failure reason |
| `frame_count` | int | number of sampled frames |
| `created_at` / `updated_at` | timestamp | |

### `frames`

The central entity. One row per sampled frame.

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | `frame_uuid`; image basename on disk |
| `video_id` (FK) | UUID | → `videos.id` |
| `frame_index` | int | index within the video's sampling sequence |
| `timestamp_s` | float | position in the source video |
| `width` / `height` | int | pixel dimensions (for normalizing boxes) |
| `queue` | enum | `training` \| `validation` (set at ingest by model) |
| `status` | enum | `unprocessed` \| `processed` |
| `model_id` (FK) | UUID, null | model that pre-labeled this frame |
| `has_ball` | bool, null | final human verdict (null until processed) |
| `created_at` / `updated_at` | timestamp | |

Indexes: `(queue, status, created_at)` powers the "give me the next
unprocessed frame in this queue" query for Tabs 2 & 3.

### `labels`

Bounding boxes. Separated from `frames` so we can keep both the model's
suggestion and the human's final answer, and to support 0..N boxes per frame
(though for pickleball it is typically 0 or 1).

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | |
| `frame_id` (FK) | UUID | → `frames.id` |
| `source` | enum | `model` (pre-label) \| `human` (final ground truth) |
| `class_id` | int | always `0` (ball) in the exported single-class dataset |
| `x_center` / `y_center` / `width` / `height` | float | **normalized** [0,1] YOLO format |
| `confidence` | float, null | for `source=model` only |
| `created_at` | timestamp | |

Rules:
- A `source=model` label is written at ingestion for validation-queue frames.
- A `source=human` label (or *zero* human labels, meaning "no ball") is written
  when a frame is marked processed in Tab 2/3.
- The **exported dataset uses `source=human`** labels exclusively (the verified
  ground truth). See [storage.md](./storage.md) and [cli.md](./cli.md).

### `models`

Registry of YOLO weight files so the app and CLI agree on "the current model".

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | |
| `version` | int | monotonically increasing (`v0001`, `v0002`, …) |
| `path` | text | relative path under `models/` |
| `is_current` | bool | exactly one row is true |
| `trained_from_export_id` | UUID, null | dataset used to train it (provenance) |
| `base_model` | text, null | weights this was fine-tuned from |
| `metrics` | json, null | mAP etc. captured at training time |
| `notes` | text, null | |
| `created_at` | timestamp | |

The app reads the current model via this table (or the `models/current`
symlink); the CLI's `set-current` flips `is_current`. See [cli.md](./cli.md).

### `ingest_jobs`

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | also the job id returned to Tab 1 |
| `video_id` (FK) | UUID, null | set once the video row exists |
| `kind` | enum | `download` \| `sample` \| `label` \| `reanalyze` |
| `state` | enum | `queued` \| `running` \| `done` \| `failed` |
| `progress` | float | 0..1 for UI progress bars |
| `payload` | json | URL / upload ref / params |
| `error` | text, null | |
| `created_at` / `updated_at` | timestamp | |

### `events` (optional audit / undo aid)

Append-only log used for the undo feature and to record model upgrades and
re-analysis runs. Each row: `id`, `kind`, `frame_id?`, `actor`, `data` (json),
`created_at`. The undo stacks in Tabs 2/3 are primarily client-side
(`labeling.max_undo_steps`), but persisting recent transitions here makes
server-side "go back" robust across reloads.

## Frame state machine

```
                 ingest + model inference
                          │
          ┌───────────────┴────────────────┐
   ball detected                       no ball
          │                                 │
          ▼                                 ▼
 queue=validation                    queue=training
 status=unprocessed                  status=unprocessed
 (model label stored)                (no label stored)
          │                                 │
   Tab 3 review                       Tab 2 review
   approve/correct                    draw box OR "no ball"
          │                                 │
          ▼                                 ▼
 status=processed                    status=processed
 has_ball=true/false                 has_ball=true/false
 human label stored                  human label stored (or none)
```

### Undo / "A" key

`A` re-opens the previously reviewed frame: set `status=unprocessed`, delete its
`source=human` labels, and (for validation) restore the model's pre-label as the
displayed box. Bounded by `labeling.max_undo_steps`. See
[ui-training.md](./ui-training.md) and [ui-validation.md](./ui-validation.md).

### Re-analysis transition

When the CLI re-analyzes the **training** backlog with a newer model, any
*unprocessed training* frame in which the new model now finds a ball is moved to
`queue=validation` (still `unprocessed`), and a fresh `source=model` label is
written. Processed frames are never touched. See [cli.md](./cli.md).
