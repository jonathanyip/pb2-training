# Tab 2 — Train

Tab 2 presents the next **unprocessed training frame** (a frame where the model
found *no* ball) and asks the human to **draw where the ball is** — or to confirm
there is none. This produces ground-truth labels, including valuable negative
samples.

## Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  [ Upload ] [ Train ] [ Validate ]            Training queue: 842 ▾ │
├───────────────────────────────────────────────┬────────────────────┤
│                                                │  How to use        │
│     ┌───────────────────────────────────┐      │                    │
│     │                                   │      │  Click + drag on   │
│     │        frame image                │      │  the image to draw │
│     │    (click-drag to draw box)       │      │  a box around the  │
│     │                                   │      │  ball.             │
│     │            ┌──────┐               │      │                    │
│     │            │ ball │               │      │ ┌────┐ A  Back/undo│
│     │            └──────┘               │      │ │ A  │            │
│     │                                   │      │ └────┘ W  Reset box│
│     └───────────────────────────────────┘      │ ┌────┐ D  Next /  │
│                                                │ │ W  │    save     │
│     frame 0f3c… · video finals_5 · 1/842       │ └────┘            │
│                                                │ ┌────┐            │
│                                                │ │ D  │  No box =  │
│                                                │ └────┘  "no ball" │
└───────────────────────────────────────────────┴────────────────────┘
```

The instructions panel is always visible. Each hotkey is also a **clickable
button** showing its letter and meaning, so the controls are discoverable
without memorizing them (satisfies the "easy to read instructions" requirement).

## Drawing the box

- The image is rendered on an HTML canvas. **Click-drag** draws a rectangle; that
  rectangle is the ball's bounding box.
- The box can be re-drawn (a new drag replaces the current box). Pickleball
  frames have at most one ball, so a single box is expected.
- Drags smaller than `labeling.box_min_size_px` are ignored (treated as a stray
  click, not a box).
- On save, the pixel box is converted to **normalized YOLO coordinates**
  (`x_center, y_center, width, height` in [0,1]) using the frame's stored
  `width`/`height` (see [data-model.md](./data-model.md)).

## Hotkeys (WASD)

| Key | Button | Action |
|-----|--------|--------|
| **D** | Next / Save | Save the current frame and advance to the next unprocessed training frame. **If no box was drawn, the frame is recorded as having no ball** (a negative sample). |
| **A** | Back / Undo | Go back to the **previous** frame. Mark the current frame `unprocessed` again and **reset any training data associated with it** (delete its human label). |
| **W** | Reset box | Clear the box currently drawn on this frame (does not advance). |

These map exactly to the problem statement: *A = go back/undo and reset, D = next
(empty box ⇒ no ball), W = clear the box.*

### What "D" (next/save) writes

```
frame.status   = processed
frame.has_ball = (a box was drawn)
if box drawn:  insert labels row (source=human, class_id=0, normalized box)
else:          insert NO labels  (negative sample; empty .txt on export)
```

### What "A" (back/undo) does

- Pops the client-side undo stack (bounded by `labeling.max_undo_steps`).
- For the frame being returned to: set `status=unprocessed`, delete its
  `source=human` label(s), so the user can redo it from scratch.
- If the undo stack is empty (e.g. fresh session), "A" is a no-op with a brief
  toast: "Nothing to undo."

See the frame state machine in [data-model.md](./data-model.md).

## Fetching the next frame

The client requests `GET /frames/next?queue=training`, which returns the oldest
`unprocessed` training frame (ordered by the `(queue, status, created_at)`
index) plus its image URL and dimensions. Autosave (`labeling.autosave`) means
"D" performs the save and the fetch in one round trip
(`POST /frames/{id}/label` then next).

## Out of frames

When no unprocessed training frames remain, the canvas is replaced with a clear
message:

> **No more training frames.** Head to the **Upload** tab to add more videos.

This is the behavior the problem statement asks for in Tabs 2 & 3.

## Endpoints used

See [api.md](./api.md): `GET /frames/next?queue=training`,
`GET /frames/{id}/image`, `POST /frames/{id}/label`, `POST /frames/{id}/reopen`
(undo), `GET /frames/count?queue=training&status=unprocessed`.
