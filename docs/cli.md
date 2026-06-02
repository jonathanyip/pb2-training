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
| `pb2 export` | Build a YOLO-format dataset from verified labels in the DB |
| `pb2 train` | Train/fine-tune a new model (LibreYOLO) on an export |
| `pb2 models ls` | List registered models and show the current one |
| `pb2 set-current <version|path>` | Make a model the app's active model |
| `pb2 reanalyze` | Re-run the current (or chosen) model over the unprocessed **training** backlog |
| `pb2 db migrate` | Apply DB migrations (also run on app startup) |
| `pb2 stats` | Counts per queue/status, per video, label totals |

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

```
pb2 export --out-id auto            # prints datasets/<export_uuid>
```

## `pb2 train`

Fine-tunes a model on an export using
[LibreYOLO](https://github.com/LibreYOLO/libreyolo) (MIT-licensed, reads
standard YOLO-format datasets).

```
pb2 train \
  --dataset datasets/<export_uuid> \   # or --export auto to export first
  --base   yolov8n.pt \                # training.base_weights
  --epochs 100 --imgsz 640 --batch 16  # override config training.*
```

Steps:
1. (Optional) run `export` first if `--export auto`.
2. Call LibreYOLO training with `training.*` settings; weights land in
   `models/vNNNN.pt` (next version number).
3. Insert a `models` row: `version`, `path`, `trained_from_export_id`,
   `base_model`, captured `metrics` (mAP, etc.). It is **not** current yet.

Conceptually:
```python
from libreyolo import LibreYOLO
model = LibreYOLO(cfg.training.base_weights)
model.train(data=f"{dataset}/data.yaml", epochs=cfg.training.epochs,
            imgsz=cfg.training.imgsz, batch=cfg.training.batch,
            device=cfg.training.device)
# best weights -> models/vNNNN.pt -> insert models row
```

## `pb2 set-current`

Promotes a trained model to be the one the app uses for pre-labeling.

```
pb2 set-current 4          # by version, or:
pb2 set-current models/v0004.pt
```

- Flips `is_current` in the `models` table to the chosen row.
- Repoints the `models/current` symlink (what `model.current_path` resolves to —
  see [configuration.md](./configuration.md)).
- The web app and worker pick up the new model on their next model load; no code
  change and no redeploy needed — the model is a hot-swappable artifact.

## `pb2 reanalyze`

After upgrading the model, re-examine frames the **old** model missed. This is
the explicit "re-analyze all unprocessed training frames" step, run separately
as a CLI step.

```
pb2 reanalyze [--model current] [--video <uuid>] [--dry-run]
```

Behavior — for every frame with `queue=training AND status=unprocessed`:
```
detections = current_model.infer(frame)
if ball (class 32) found:
    frame.queue  = validation        # auto-moved to validation backlog
    frame.status = unprocessed        # still needs a quick human approval
    write label(source=model, <box>, confidence)
    frame.model_id = current_model.id
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
pb2 stats                       # see how much labeled data exists
pb2 train --export auto         # export verified labels + train vNNNN
pb2 models ls                   # inspect metrics
pb2 set-current <new version>   # hot-swap the app's model
pb2 reanalyze                   # promote missed frames to the validate queue
```

Then humans return to Tabs 2 & 3 to clear the (now smaller, easier) backlog, and
the loop repeats.
