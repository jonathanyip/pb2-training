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

models                — registry of named, versioned YOLO weights; one is "active"
  └─ base_model_id    — self-FK: lineage (V2 was trained from V1)
model_trained_frames  — which frames each model was trained on (incremental training)
settings              — DB-backed config edited from the Settings tab (Tab 4)
ingest_jobs           — async work items for the worker
events                — optional audit log (undo history, model upgrades)
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

Registry of **named, versioned** YOLO weights so the app and CLI agree on which
model is active and how each model was produced.

| Column | Type | Notes |
|--------|------|-------|
| `id` (PK) | UUID | |
| `name` | text, unique | human label, e.g. `YOLOX_V1`, `YOLOX_V2` |
| `version` | int | monotonically increasing (`v0001`, `v0002`, …) |
| `path` | text | relative path under `models/` |
| `is_active` | bool | exactly one row is true — the model used for post-analysis |
| `is_bootstrap` | bool | true for the seeded first model (no training data behind it) |
| `base_model_id` | UUID, null | self-FK → `models.id`; the model this was fine-tuned from (lineage). Null for a bootstrap model |
| `trained_from_export_id` | UUID, null | dataset snapshot used to train it (provenance) |
| `base_weights` | text, null | external weights file fine-tuned from (e.g. `yolov8n.pt`) when there is no parent model |
| `metrics` | json, null | mAP etc. captured at training time |
| `notes` | text, null | |
| `created_at` | timestamp | |

- The **active** model (`is_active`) is what ingestion/post-analysis and
  `reanalyze` use for pre-labeling. It is selectable from the **Settings tab**
  (see [ui-settings.md](./ui-settings.md)) and via the CLI's `set-active`.
- `base_model_id` records lineage: "`YOLOX_V2` was trained from `YOLOX_V1`". This
  is what lets incremental training know an ancestor chain to subtract already-
  trained frames from (see below and [cli.md](./cli.md)).
- `is_bootstrap` marks the seeded first model so the UI/CLI can show that it has
  no training provenance.

### `model_trained_frames`

The **frame-tracking mechanism** that records, for each model, exactly which
frames went into its training set. This is what enables *incremental* training:
"`YOLOX_V1` trained on frames 1..1000, `YOLOX_V2` trained on 1001..5000", so a
new model only trains on frames not already learned by its lineage.

| Column | Type | Notes |
|--------|------|-------|
| `model_id` (FK) | UUID | → `models.id` |
| `frame_id` (FK) | UUID | → `frames.id` |
| `created_at` | timestamp | when this frame was assigned to the model's training set |

Primary key `(model_id, frame_id)`; indexed both ways. Populated by `pb2 train`
when a model is created (see [cli.md](./cli.md)).

**Computing "new frames" for the next model.** To train `YOLOX_V2` from
`YOLOX_V1`, the CLI selects the eligible labeled frames and subtracts every frame
already trained by `YOLOX_V1` and its ancestors:

```
eligible      = frames where status=processed            (verified ground truth)
already_known = union of model_trained_frames.frame_id
                for V1 and every ancestor via base_model_id
new_frames    = eligible − already_known
```

`YOLOX_V2` then trains on `new_frames` (optionally still validating against a
held-out split), and a new `model_trained_frames` row is written for each frame
in `new_frames`. Because membership is an explicit set rather than an index
range, it stays correct even when frames are added, deleted, or re-labeled out of
order.

### `settings`

DB-backed configuration edited from the **Settings tab** (Tab 4). This replaces
the static config file as the runtime source of truth; the file now only seeds
defaults on first boot (see [configuration.md](./configuration.md)).

| Column | Type | Notes |
|--------|------|-------|
| `key` (PK) | text | dotted key, e.g. `sampling.every_n_frames` |
| `value` | json | typed value |
| `updated_at` | timestamp | |
| `updated_by` | text, null | optional actor |

A single-row variant (`settings(id=1, data json)`) is equally acceptable; the
key/value form makes partial updates and auditing easier. The active-model
selection is stored as `models.is_active` (not in `settings`) so referential
integrity is enforced by a real FK.

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
