# Ingestion & post-processing pipeline

Ingestion turns a source (YouTube URL or uploaded file) into sampled, pre-labeled
frames sorted into the training/validation queues. It runs **asynchronously** in
the worker so the Upload tab stays responsive (see
[architecture.md](./architecture.md)).

## Trigger

Tab 1 submits either a list of YouTube URLs or an uploaded file. The API:

1. Creates a `videos` row (`status=pending`) with a new `video_uuid`.
2. Creates an `ingest_jobs` row and returns its `job_id`.
3. Returns immediately; Tab 1 tracks progress via polling / WebSocket
   (see [ui-upload.md](./ui-upload.md) and [api.md](./api.md)).

## Pipeline stages

```
 ┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────────┐
 │ 1. Acquire  │──▶│ 2. Probe     │──▶│ 3. Sample     │──▶│ 4. Pre-label     │
 │ download/   │   │ fps,duration │   │ frames →      │   │ YOLO per frame → │
 │ store source│   │ dimensions   │   │ frames/<vid>/ │   │ route to queue   │
 └─────────────┘   └──────────────┘   └───────────────┘   └──────────────────┘
        │                                                          │
   video.status:                                            video.status:
   downloading → ...                                        labeling → ready
```

### 1. Acquire

- **YouTube:** `yt_dlp` downloads to `videos/<video_uuid>/source.<container>`
  using `ingestion.youtube.format`. The resolved title is saved to
  `videos.title`, the URL to `videos.source_url`.
- **Upload:** the uploaded bytes are streamed to
  `videos/<video_uuid>/source.<container>`. The original name is kept in
  `videos.original_filename` (display only). Because the directory is a UUID,
  **overlapping filenames never collide** (see [storage.md](./storage.md)).

`video.status` moves `pending → downloading`. On failure: `failed` + `error`.

### 2. Probe

`ffmpeg`/OpenCV reads `fps`, `duration_s`, and frame dimensions, stored on the
`videos` row. Dimensions are later copied onto each `frames` row so bounding
boxes can be normalized.

### 3. Sample frames

Driven by `sampling.*` config:

- `mode=every_n_frames` → keep every Nth decoded frame.
- `mode=fps` → resample to `target_fps`.
- `mode=interval_seconds` → one frame every `interval_seconds`.

Each kept frame is:
- assigned a `frame_uuid`,
- written to `frames/<video_uuid>/<frame_uuid>.jpg`
  (`image_format`/`jpeg_quality`),
- inserted as a `frames` row (`frame_index`, `timestamp_s`, `width`, `height`,
  `status=unprocessed`, queue TBD in stage 4).

`max_frames_per_video` caps output if set. `video.status = sampling`.

### 4. Pre-label & route

Load the **current model** (`model.current_path`) once, then for each sampled
frame run inference (`model.inference.*`) and look for the configured
`model.ball_class_id` (32 = sports ball):

```
detections = model.infer(frame, conf=conf_threshold, iou=iou_threshold)
ball = highest-confidence detection with class_id == ball_class_id

if ball is not None:
    frame.queue  = validation
    write label(source=model, class_id=0, <ball bbox normalized>, confidence)
else:
    frame.queue  = training
    # no label stored; a human will draw one (or confirm "no ball")

frame.model_id = current_model.id
frame.status   = unprocessed
```

This is the fork described in [overview.md](./overview.md): **model found a ball
→ validation queue (box pre-filled); model found nothing → training queue (box
empty).**

When all frames are labeled, `video.status = ready` and the job is `done`.

## Batching & performance

- The model is loaded **once per job** and frames are processed in batches
  (`inference.imgsz`, optional `half` precision, `device`) for throughput.
- `ingestion.max_concurrent_jobs` bounds parallel videos; on a single GPU keep
  this low to avoid VRAM contention.
- Sampling and inference can be fused (sample → immediately infer) to avoid a
  second decode pass; the doc presents them as stages for clarity.

## Idempotency & failure handling

- Each stage records progress on the `ingest_jobs` row (`progress`, `state`).
- A failed job leaves `video.status=failed` with a human-readable `error`; Tab 1
  shows it and offers **retry** (re-enqueues from the last completed stage —
  e.g. a sampled-but-not-labeled video resumes at stage 4).
- Re-running ingestion for a video is safe: frames already present (by
  `frame_index`) are skipped.

## Relationship to re-analysis

Stage 4's routing logic is the **same code** the CLI uses later to re-analyze
the training backlog with an upgraded model — it lives in `pb2core` and is
reused, so a frame routed today and a frame re-routed after a model upgrade
follow identical rules. See [cli.md](./cli.md).
