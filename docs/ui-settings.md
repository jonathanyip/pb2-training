# Tab 4 — Settings

Tab 4 makes the runtime configuration editable **in the UI**, persisted in the
database (`settings` table — see [data-model.md](./data-model.md)), and lets the
operator choose **which model is used for post-analysis**. It replaces hand-
editing a config file for everything except the tiny bootstrap file
(see [configuration.md](./configuration.md)).

## Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  [ Upload ] [ Train ] [ Validate ] [ Settings ]                    │
├────────────────────────────────────────────────────────────────────┤
│  Active model (used for post-analysis)                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ◉ YOLOX_V2   v0002  · trained from YOLOX_V1 · mAP 0.71        │  │
│  │ ○ YOLOX_V1   v0001  · trained from bootstrap · mAP 0.58       │  │
│  │ ○ bootstrap  v0000  · seeded (yolov8n.pt)                     │  │
│  │                                              [ Set active ]   │  │
│  └──────────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────────┤
│  Sampling                                                          │
│   mode [every_n_frames ▾]  every_n_frames [ 15 ]  jpeg_quality[90] │
│  Inference                                                         │
│   conf [0.25]  iou [0.45]  imgsz [640]  device [auto ▾]            │
│  Labeling                                                          │
│   max_undo_steps [20]  box_min_size_px [4]  autosave [✓]           │
│  Ingestion / UI / Training …                                       │
│                                                          [ Save ]  │
└────────────────────────────────────────────────────────────────────┘
```

## Active-model selection

- Lists every row in the `models` table with its **name** (`YOLOX_V1`,
  `YOLOX_V2`, …), version, lineage (`trained from …` via `base_model_id`), and
  metrics.
- A radio/select chooses which model is **active**; **Set active** flips
  `models.is_active` to that row (exactly one is active).
- The active model is what the ingestion/post-analysis pipeline and the CLI
  `reanalyze` use for pre-labeling (see
  [ingestion-pipeline.md](./ingestion-pipeline.md) and [cli.md](./cli.md)).
- Because selection is a DB pointer, swapping the post-analysis model takes
  effect on the worker's next model load — no file edit, no redeploy.

> The same operation is available headless via `pb2 set-active <name|version>`
> (see [cli.md](./cli.md)); the UI and CLI write the identical column.

## Editing settings

- The form is generated from the runtime settings schema in
  [configuration.md](./configuration.md) (sampling, inference, labeling,
  ingestion, ui, training).
- Each field is validated client- and server-side against the schema
  (types, ranges) before being written.
- **Save** upserts the changed keys into the `settings` table with
  `updated_at`/`updated_by`. Readers (`pb2core.config`) pick up new values on
  their next read; long-running operations (an in-flight ingest job) keep the
  values they started with.
- A **Reset to defaults** action re-seeds a key from the built-in defaults.

## What is *not* here

- `storage.root`, `database.url`, and `server.*` are **bootstrap** settings that
  must be known before the DB opens, so they stay in `config.yaml` /
  environment and are intentionally not editable here (see
  [configuration.md](./configuration.md)).

## Endpoints used

See [api.md](./api.md): `GET /settings`, `PUT /settings`, `GET /models`,
`POST /models/{id}/activate`.
