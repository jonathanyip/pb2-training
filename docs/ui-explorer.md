# Tab 4 — Explorer

The **Explorer** tab is a read-and-prune view over the frames that have already
been sampled for a video. Use it to spot-check labeling data and remove or reset
individual frames without going through the Train/Validate queues.

## Layout

The tab has two views:

1. **Video list** — the same paginated, searchable list of videos as the Upload
   tab (title, source, frame count, queue breakdown, status, added date). Click a
   row to drill into its frames.
2. **Frame grid** — a thumbnail grid of the selected video's frames. Each
   thumbnail is rendered on a `<canvas>` with the frame's labels drawn on top:
   **human** boxes in blue, **model** boxes in amber. A **‹** back button returns
   to the video list.

## Filters

Two segmented controls filter the grid (both default to "all"):

- **Status** — `All`, `Processed`, `Unprocessed`.
- **Ball** — `Any`, `Ball`, `No ball`, `Unreviewed`. "Unreviewed" is a frame that
  has never been human-reviewed (`has_ball IS NULL`); "No ball" is a frame a
  human explicitly marked empty (`has_ball = false`).

Each card shows the frame index and a verdict badge: `ball`, `no ball`, or
`unreviewed`.

## Per-frame actions

| Button | Action | Effect |
|--------|--------|--------|
| **↺** | Re-open | `POST /frames/{id}/reopen` — sets the frame back to `unprocessed` so it re-enters the Train/Validate queue. Keeps existing labels. |
| **⦸** | No ball | `POST /frames/{id}/clear` — deletes the human labels and records the frame as reviewed-empty (`status=processed`, `has_ball=false`). |
| **🗑** | Delete | `DELETE /frames/{id}` — permanently removes the frame, its labels, its `model_trained_frames` rows, and the JPEG, and decrements the video's `frame_count`. Confirmed via a dialog. |

Delete is for frames that should never be used for training (e.g. corrupt or
irrelevant samples). Re-open and No-ball are non-destructive corrections that
keep the frame in the dataset.

See [api.md](./api.md) for the endpoint contracts and
[data-model.md](./data-model.md) for the frame state machine.
