# Backend API

The backend is a FastAPI service. All endpoints are JSON unless noted. Paths are
relative to the server root (e.g. `/api/v1`). This reference is the contract the
four tabs in the SPA consume; the auto-generated OpenAPI doc is the source of
truth at runtime.

Conventions:
- IDs are UUID strings.
- Bounding boxes are **normalized YOLO** floats `{x_center, y_center, width,
  height}` in `[0,1]` with `class_id` (always `0` for ball in exports).
- Errors return `{ "error": "<message>" }` with an appropriate HTTP status.

## Videos & ingestion (Tab 1)

### `POST /videos`
Add one or more YouTube videos.
```json
// request
{ "urls": ["https://youtu.be/aaa", "https://youtu.be/bbb"] }
// response
{ "videos": [ { "id": "uuid", "job_id": "uuid", "status": "pending" }, ... ] }
```

### `POST /videos/upload`
Multipart upload of a local video file (streamed/chunked). Returns the created
video and its ingest job.
```json
{ "id": "uuid", "job_id": "uuid", "status": "pending" }
```

### `GET /videos?page=&size=&q=`
Paginated list for the Tab 1 table (`size` defaults to `ui.pagination_size`).
```json
{
  "page": 1, "size": 24, "total": 137,
  "items": [
    { "id": "uuid", "title": "game.mp4", "source_type": "upload",
      "frame_count": 412, "status": "ready",
      "queue_breakdown": { "training": 120, "validation": 292 },
      "created_at": "..." }
  ]
}
```

### `GET /videos/{id}` · `DELETE /videos/{id}`
Fetch detail / delete a video and all its frames, labels, and files.

### `POST /videos/{id}/retry`
Re-enqueue a failed video from its last completed stage.

### `GET /jobs/{id}`
Ingestion job status for progress bars / live updates.
```json
{ "id": "uuid", "kind": "sample", "state": "running", "progress": 0.62,
  "error": null }
```

### `WS /ws/jobs`
WebSocket stream of job/video status changes so Tab 1 updates without polling.
Polling `GET /jobs/{id}` every `ui.poll_interval_ms` is the fallback.

## Frames & labeling (Tabs 2 & 3)

### `GET /frames/next?queue=training|validation`
Returns the oldest `unprocessed` frame in the queue, or `204 No Content` /
`{ "frame": null }` when the queue is empty (drives the "no more frames"
message).
```json
{
  "frame": {
    "id": "uuid", "video_id": "uuid", "video_title": "finals_5",
    "image_url": "/api/v1/frames/uuid/image",
    "width": 1920, "height": 1080,
    "queue": "validation",
    "prelabel": { "class_id": 0, "x_center": 0.51, "y_center": 0.33,
                  "width": 0.04, "height": 0.06, "confidence": 0.78 }
  },
  "remaining": 311
}
```
`prelabel` is present for validation frames and `null` for training frames.

### `GET /frames/{id}/image`
Returns the JPEG bytes for the frame (served from
`frames/<video_uuid>/<frame_uuid>.jpg`).

### `POST /frames/{id}/label`
Save the human verdict and mark the frame processed (the **D** action). Send an
empty `boxes` array to record "no ball".
```json
// request
{ "boxes": [ { "class_id": 0, "x_center": 0.50, "y_center": 0.34,
               "width": 0.045, "height": 0.06 } ] }
// response
{ "id": "uuid", "status": "processed", "has_ball": true }
```

### `POST /frames/{id}/reopen`
The **A** (back/undo) action: set the frame back to `unprocessed`, delete its
`source=human` labels. For validation frames the model pre-label remains and is
returned again on the next fetch.
```json
{ "id": "uuid", "status": "unprocessed" }
```

### `GET /frames/count?queue=&status=`
Queue size for the header badge (e.g. `queue=training&status=unprocessed`).
```json
{ "count": 842 }
```

## Settings (Tab 4)

### `GET /settings`
Returns the effective runtime settings from the `settings` table (see
[configuration.md](./configuration.md)) plus their schema for form rendering.
```json
{ "values": { "sampling.every_n_frames": 15, "labeling.max_undo_steps": 20, ... },
  "schema": { "sampling.every_n_frames": { "type": "int", "min": 1 }, ... } }
```

### `PUT /settings`
Upsert one or more runtime settings (validated against the schema). Bootstrap
keys (`storage.*`, `database.*`, `server.*`) are rejected.
```json
// request
{ "values": { "sampling.every_n_frames": 10, "labeling.max_undo_steps": 30 } }
// response
{ "updated": ["sampling.every_n_frames", "labeling.max_undo_steps"] }
```

## Models

### `GET /models`
List registered models with name, lineage, and which is active.
```json
{ "active": { "id": "uuid", "name": "YOLOX_V2", "version": 2,
              "base_model": "YOLOX_V1" },
  "items": [
    { "id": "uuid", "name": "YOLOX_V2", "version": 2, "is_active": true,
      "base_model_id": "uuid", "trained_frames": 4000, "metrics": {"map50": 0.71} },
    { "id": "uuid", "name": "YOLOX_V1", "version": 1, "is_active": false,
      "base_model_id": null, "trained_frames": 1000, "metrics": {"map50": 0.58} },
    { "id": "uuid", "name": "bootstrap", "version": 0, "is_bootstrap": true,
      "trained_frames": 0 }
  ] }
```

### `POST /models/{id}/activate`
Make the given model the active one used for post-analysis (the Settings tab's
"Set active"; mirrors `pb2 set-active`). Flips `models.is_active`.
```json
{ "id": "uuid", "name": "YOLOX_V2", "is_active": true }
```

Model **creation**, **training**, and **bootstrap** are performed by the **CLI**,
not the API, to keep training/ops out of the request path. The API only lists
models and flips the active pointer. See [cli.md](./cli.md).

## Notes

- Authentication is out of scope for the core design (single-team deployment);
  if needed, a token/middleware can wrap all routes — see
  [deployment.md](./deployment.md).
- The undo depth is enforced primarily client-side
  (`labeling.max_undo_steps`); `POST /frames/{id}/reopen` is idempotent and safe
  to call for any processed frame, which keeps undo robust across reloads (the
  optional `events` table records history — see [data-model.md](./data-model.md)).
