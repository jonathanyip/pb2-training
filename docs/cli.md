# CLI tool — `pb2`

The CLI is the operator's interface to the **progressive training loop**. It runs
in the same image as the app and imports the same `pb2core` library, so it reads
the identical database schema and storage layout the web app uses (this answers
the problem statement's "the CLI tool also needs to know how the database is
structured… it should share library code" — it shares **all** of `pb2core`; see
[architecture.md](./architecture.md)).

```
pb2 --config /data/config.yaml <command> [options]
```

## Command summary

| Command | Purpose |
|---------|---------|
| `pb2 bootstrap` | Seed the **first** model from external weights (e.g. `yolov8n.pt`) |
| `pb2 export` | Build a YOLO-format dataset from verified labels in the DB |
| `pb2 train` | Train a new named model (LibreYOLO); incrementally from a parent |
| `pb2 models ls` | List registered models, lineage, and which is active |
| `pb2 set-active <name|version>` | Make a model the one used for post-analysis |
| `pb2 reanalyze` | Re-run the active (or chosen) model over the unprocessed **training** backlog |
| `pb2 db migrate` | Apply DB migrations (also run on app startup) |
| `pb2 stats` | Counts per queue/status, per video, per model trained-frame totals |

## `pb2 bootstrap`

Creates the **first** model so post-analysis works before any human labeling
exists. The problem statement calls this out: "we should have the ability to
bootstrap a first model."

```
pb2 bootstrap --name YOLOX_V1 --weights yolov8n.pt   # weights = model.bootstrap_weights
```

- Copies/links the external weights into `models/v0000.pt`.
- Inserts a `models` row: `name`, `is_bootstrap=true`, `base_model_id=null`,
  `base_weights=<weights>`.
- If no model is active yet, marks it `is_active=true`.
- Writes **no** `model_trained_frames` rows — a bootstrap model has trained on
  nothing, so *every* labeled frame is "new" for the first real training run.

## `pb2 export`

Materializes the human-verified labels into a YOLO dataset on disk under
`datasets/<export_uuid>/` (see [storage.md](./storage.md)).

- Selects frames with `status=processed`.
- Writes `images/{train,val}/<frame_uuid>.jpg` and
  `labels/{train,val}/<frame_uuid>.txt` (normalized `0 cx cy w h`).
- Frames with `has_ball=false` produce **empty** `.txt` files (negative
  samples).
- Splits train/val by `training.val_split`.
- Emits `data.yaml` (single class: `ball`).
- Records a `models`-adjacent export record (or returns the `export_uuid`) for
  provenance.
- Accepts `--frames-from <selector>` so `pb2 train` can export only the *new*
  frames it computed (see below).


```
pb2 export --out-id auto            # prints datasets/<export_uuid>
```

## `pb2 train`

Trains a **new named model** on the verified labels using
[LibreYOLO](https://github.com/LibreYOLO/libreyolo) (MIT-licensed, reads standard
YOLO-format datasets). Supports **incremental** training: generate `YOLOX_V2`
from `YOLOX_V1`, training only on frames `V1` (and its ancestors) never trained
on.

```
pb2 train --name YOLOX_V2 --from YOLOX_V1     # incremental from a parent model
pb2 train --name YOLOX_V1 --from bootstrap    # first real model (parent = bootstrap)
pb2 train --name YOLOX_V1 --full              # train on ALL labeled frames
```

Steps:
1. **Resolve the parent** (`--from`). Fine-tuning starts from the parent model's
   weights (`models.path`); if the parent is the bootstrap model, that means the
   `bootstrap_weights`. `--full` falls back to `training.base_weights`.
2. **Compute the new-frame set** (the incremental core — see
   [data-model.md](./data-model.md)):
   ```
   eligible      = frames with status=processed
   already_known = union of model_trained_frames.frame_id over the parent
                   and all its ancestors (walk base_model_id)
   new_frames    = eligible − already_known        # unless --full, which uses eligible
   ```
   If `new_frames` is empty, the command stops with "nothing new to train on."
3. **Export** just `new_frames` to `datasets/<export_uuid>/` (reusing
   `pb2 export --frames-from new`).
4. **Train** with LibreYOLO starting from the parent's weights; best weights land
   in `models/vNNNN.pt`.
5. **Register** the model: insert a `models` row with `name`, `version`, `path`,
   `base_model_id=<parent>`, `trained_from_export_id`, `metrics`. It is **not**
   active yet (use `set-active`).
6. **Record trained frames:** insert one `model_trained_frames(model_id,
   frame_id)` row for every frame in `new_frames`. This is the bookkeeping that
   lets the *next* model skip them — i.e. `V1` is recorded against 1..1000, `V2`
   against 1001..5000, and so on.

Conceptually:
```python
from libreyolo import LibreYOLO
parent = resolve_model(args.from_)                  # weights to fine-tune from
new_frames = eligible_frames() - trained_by(parent.lineage())
dataset = export(new_frames)                        # YOLO-format snapshot
model = LibreYOLO(parent.weights_path)
model.train(data=f"{dataset}/data.yaml", epochs=cfg.training.epochs,
            imgsz=cfg.training.imgsz, batch=cfg.training.batch,
            device=cfg.training.device)
m = register_model(name=args.name, base_model_id=parent.id, path="models/vNNNN.pt")
record_trained_frames(m.id, new_frames)             # -> model_trained_frames
```

> **Why a frame set, not an index range?** The problem statement frames this as
> "`V1` processed 1..1000, `V2` 1000..5000." We track the *actual frame UUIDs*
> per model in `model_trained_frames` rather than a numeric range, so the
> accounting stays correct even if frames are added out of order, deleted, or
> re-labeled. The effect is the same — each new model only trains on genuinely
> new data — but it is robust. `--full` is always available to retrain on
> everything.

## `pb2 set-active`

Selects which model post-analysis uses (the same action as the Settings tab's
"Set active" — see [ui-settings.md](./ui-settings.md)).

```
pb2 set-active YOLOX_V2     # by name, or:
pb2 set-active 2            # by version
```

- Flips `is_active` in the `models` table to the chosen row (exactly one active).
- Optionally repoints a convenience `models/current` symlink to the active
  weights.
- The web app and worker pick up the new model on their next model load; no code
  change and no redeploy needed — the model is a hot-swappable artifact.

## `pb2 reanalyze`

After activating a better model, re-examine frames the **old** model missed. This
is the explicit "re-analyze all unprocessed training frames" step, run separately
as a CLI step.

```
pb2 reanalyze [--model active] [--video <uuid>] [--dry-run]
```

Behavior — for every frame with `queue=training AND status=unprocessed`:
```
detections = active_model.infer(frame)
if ball (class 32) found:
    frame.queue  = validation        # auto-moved to validation backlog
    frame.status = unprocessed        # still needs a quick human approval
    write label(source=model, <box>, confidence)
    frame.model_id = active_model.id
else:
    leave it in the training queue (still needs manual labeling)
```

- **Processed frames are never touched** — already-verified ground truth is
  preserved.
- It uses the *same* routing function as ingestion stage 4
  ([ingestion-pipeline.md](./ingestion-pipeline.md)), so rules can't drift.
- `--dry-run` reports how many frames *would* move without writing.
- Net effect: a better model promotes previously-missed frames from the
  expensive **draw-from-scratch** (Train) queue into the cheap **approve**
  (Validate) queue, shrinking future manual effort — the core of the
  progressive loop in [overview.md](./overview.md).

## Typical operator session

```
# One-time, on a fresh install:
pb2 bootstrap --name YOLOX_V1 --weights yolov8n.pt   # seed first model, set active
# ... operators ingest videos and label in Tabs 2 & 3 ...

pb2 stats                              # see labeled data + per-model trained counts
pb2 train --name YOLOX_V2 --from YOLOX_V1   # train only on frames new since V1
pb2 models ls                          # inspect lineage + metrics
pb2 set-active YOLOX_V2                 # hot-swap the post-analysis model
pb2 reanalyze                          # promote missed frames to the validate queue
```

Then humans return to Tabs 2 & 3 to clear the (now smaller, easier) backlog, and
the loop repeats — each `pb2 train --from <previous>` only learns the new frames,
thanks to `model_trained_frames`.
