# Tab 3 — Validate

Tab 3 is nearly identical to Tab 2 in UX, but it works the **validation queue**:
frames where the model *did* find a ball. The model's bounding box is
**pre-populated** on the image and the human's job is to confirm it is correct —
or fix it. This verifies what the YOLO model has already found.

## Layout

Same split as Tab 2 (image canvas + persistent instructions panel), with the box
already drawn from the model's pre-label:

```
┌────────────────────────────────────────────────────────────────────┐
│  [ Upload ] [ Train ] [ Validate ]          Validation queue: 311 ▾ │
├───────────────────────────────────────────────┬────────────────────┤
│     ┌───────────────────────────────────┐      │  How to use        │
│     │        frame image                │      │                    │
│     │     ┌───────┐  ← model's box       │      │ The box is the     │
│     │     │ ball  │    (pre-filled)      │      │ model's guess.     │
│     │     └───────┘                      │      │ Confirm or fix it. │
│     └───────────────────────────────────┘      │                    │
│     frame 9b21… · video game.mp4 · 1/311       │ A  Back / undo     │
│                                                │ W  Reset box       │
│                                                │ D  Approve / next  │
└───────────────────────────────────────────────┴────────────────────┘
```

## Pre-populated box

- On load, the frame's `source=model` label (stored at ingestion — see
  [ingestion-pipeline.md](./ingestion-pipeline.md)) is rendered as the initial
  box, denormalized to pixels using the frame's `width`/`height`.
- The user may **adjust** it (drag a new box to replace it) if the model was
  slightly off, then approve.

## Hotkeys (WASD)

| Key | Button | Action |
|-----|--------|--------|
| **D** | Approve / Next | Accept the box currently shown (model's, or the user's corrected one) as ground truth, mark the frame processed, and advance. |
| **A** | Back / Undo | Go back to the **previous** validation frame. Clear the user-provided data and **restore the model's original box**, leaving it unprocessed to review again. |
| **W** | Reset box | Reset the box back to the model's original pre-label (clear user edits) — does not advance. |

This matches the problem statement: *D approves and advances, A undoes back to a
previous validation and reverts to the model-provided box, W resets the box.*

> Note the subtle difference from Tab 2's **W**: in Train, "reset" clears to an
> empty box (there was no model box); in Validate, "reset" restores the model's
> box. Both mean "discard my current edits."

### What "D" (approve/next) writes

```
frame.status   = processed
frame.has_ball = true
insert/replace labels row (source=human, class_id=0, <approved box normalized>)
```

Approving converts the model's suggestion into a verified `source=human` label,
which is what the dataset export consumes (see [data-model.md](./data-model.md)).

### What "A" (back/undo) does

- Bounded by `labeling.max_undo_steps`.
- Re-opens the previous frame: `status=unprocessed`, delete its `source=human`
  label, and re-display the original `source=model` box so the reviewer starts
  over from the model's guess.

### Rejecting a bad detection

If the model boxed something that is **not** the ball, the reviewer clears the
box with **W** and then... there is no ball to mark, so pressing **D** with an
empty box records `has_ball=false` (a false-positive correction → negative
sample). This keeps Validate symmetric with Train.

## Fetching, out-of-frames, endpoints

Identical mechanics to Tab 2 but with `queue=validation`:

- `GET /frames/next?queue=validation` returns the next unprocessed validation
  frame **and its model pre-label**.
- When the queue is empty:

  > **No more frames to validate.** Add more videos in the **Upload** tab.

- Endpoints: see [api.md](./api.md) — same set as Tab 2 with
  `queue=validation`, plus the pre-label is included in the frame payload.
