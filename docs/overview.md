# Overview

## Problem

Training a YOLO model to track a fast-moving pickleball requires a large amount
of labeled image data. Generic pre-trained models (COCO) can sometimes detect a
"sports ball" (class id `32`), but they miss the ball frequently, especially
when it is blurred, small, or against a busy background. Hand-labeling thousands
of frames from scratch is slow.

`pb2-training` accelerates this by combining **machine pre-labeling** with
**human-in-the-loop correction**, and then **progressively re-training** the
model on the growing corpus so each iteration requires less manual work.

## Goals

- Ingest match footage from **YouTube URLs** or **local file uploads**.
- Organize videos and sampled frames on disk in a predictable, collision-free
  layout.
- Track everything in a database keyed by stable UUIDs.
- Pre-label sampled frames with the **active** YOLO model and route them into one
  of two human-review queues.
- Provide fast, keyboard-driven (WASD) UIs for labeling and validating, plus a
  Settings tab to edit configuration and pick the active model.
- Offer a CLI to bootstrap a first model and to train each new model
  *incrementally* on only the frames not already trained, swap it in, and
  re-analyze old frames with the improved model.

## Non-goals

- Real-time / live-stream inference (this is an offline dataset-building tool).
- Multi-class detection. The app cares about a single class: **ball**.
- Multi-tenant accounts / auth (assumed single-team, trusted deployment). A
  thin auth layer can be added later; see [deployment.md](./deployment.md).

## Glossary

| Term | Meaning |
|------|---------|
| **Video** | A source clip (downloaded from YouTube or uploaded locally). |
| **Frame** | A single still image sampled from a video, tracked by UUID. |
| **Pre-label** | A bounding box produced automatically by the YOLO model. |
| **Training frame** | A sampled frame where the model found **no** ball. A human must draw the box (or confirm there is no ball). |
| **Validation frame** | A sampled frame where the model **did** find a ball. A human confirms/corrects the box. |
| **Processed / Unprocessed** | Whether a human has finished reviewing a frame. The review queues only show *unprocessed* frames. |
| **Active model** | The model row (`models.is_active`) the app uses for pre-labeling, chosen in the Settings tab or via `pb2 set-active`. |
| **Bootstrap model** | The seeded first model (external weights, e.g. `yolov8n.pt`) that has no training data behind it. |
| **Model lineage** | `V2` is trained *from* `V1` (`base_model_id`); the chain of ancestors. |
| **Trained-frame set** | Per-model record (`model_trained_frames`) of exactly which frames a model trained on, enabling incremental training. |
| **Dataset export** | A YOLO-format dataset (images + `.txt` label files + `data.yaml`) generated from the database for training. |

## The two review queues

After ingestion, every sampled frame is sorted by the model into exactly one
queue:

```
                       ┌─────────────────────────┐
   sampled frame ──▶   │  active YOLO model       │
                       │  detects class 32 (ball)?│
                       └───────────┬──────────────┘
                      yes ◀────────┴────────▶ no
                       │                       │
             ┌─────────▼──────────┐  ┌─────────▼──────────┐
             │ VALIDATION queue   │  │  TRAINING queue    │
             │ (box pre-filled)   │  │  (box empty)       │
             │ Tab 3 — approve    │  │  Tab 2 — draw box  │
             └────────────────────┘  └────────────────────┘
```

- **Validation (Tab 3):** "The model thinks the ball is *here* — is it right?"
- **Training (Tab 2):** "The model saw nothing — show it where the ball is (or
  confirm there is none)."

Both human actions produce ground-truth labels stored in the database.

## End-to-end workflow

```
 ┌──────────┐   ┌─────────────┐   ┌──────────────────────────┐
 │ Tab 1    │   │ Ingestion   │   │ Tab 2 (Train)            │
 │ Upload   │──▶│ sample +    │──▶│ Tab 3 (Validate)         │──┐
 │ videos   │   │ pre-label   │   │ humans produce labels    │  │
 └──────────┘   └─────────────┘   └──────────────────────────┘  │
                                                                 │
        ┌────────────────────────────────────────────────────────┘
        ▼
 ┌──────────────────────┐   ┌──────────────────────────┐   ┌──────────────────┐
 │ CLI: export new      │──▶│ CLI: train V_n+1 from V_n │──▶│ CLI: set-active  │
 │ frames from database │   │ (LibreYOLO, new frames    │   │ swap weights     │
 │                      │   │  only via lineage)        │   │                  │
 └──────────────────────┘   └──────────────────────────┘   └────────┬─────────┘
                                                                 │
                                          ┌──────────────────────▼─────────┐
                                          │ CLI: reanalyze unprocessed     │
                                          │ training frames with new model │
                                          │ → some move to validation queue│
                                          └────────────────────────────────┘
```

This is the **progressive training loop**: each pass labels data, trains a
better model **on only the newly-labeled frames** (tracked per model in
`model_trained_frames`), and the better model auto-promotes previously-missed
frames into the (cheaper) validation queue, reducing future manual effort.

See [cli.md](./cli.md) for the mechanics of training, model swap, and
re-analysis.
