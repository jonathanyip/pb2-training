# Tab 1 — Upload

Tab 1 is where users add source videos and watch them get ingested. It has two
regions: an **add-videos** panel and a **paginated list** of all videos added so
far.

## Layout

```
┌────────────────────────────────────────────────────────────────────┐
│  [ Upload ] [ Train ] [ Validate ]                                  │
├────────────────────────────────────────────────────────────────────┤
│  Add videos                                                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ ◉ YouTube URLs   ○ Upload file                                │  │
│  │ ┌──────────────────────────────────────────────────────────┐ │  │
│  │ │ https://youtu.be/aaa                                      │ │  │
│  │ │ https://youtu.be/bbb   (one URL per line — batch add)     │ │  │
│  │ └──────────────────────────────────────────────────────────┘ │  │
│  │                                            [ Add to queue ]   │  │
│  └──────────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────────┤
│  Videos (137)                              page 1 / 6   [‹] [›]     │
│  ┌────────────┬────────────┬───────────┬──────────┬──────────────┐  │
│  │ Title      │ Source     │ Frames    │ Status   │ Progress     │  │
│  ├────────────┼────────────┼───────────┼──────────┼──────────────┤  │
│  │ game.mp4   │ upload     │ 412       │ ready    │ ████████████ │  │
│  │ game.mp4   │ youtube    │ 0         │ sampling │ ███████░░░░░ │  │
│  │ finals_5   │ youtube    │ 980       │ ready    │ ████████████ │  │
│  └────────────┴────────────┴───────────┴──────────┴──────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

Note the two `game.mp4` rows: duplicate titles coexist because each is a
distinct `video_uuid` (see [storage.md](./storage.md)).

## Adding videos

### YouTube
- A textarea accepts **one or more URLs**, one per line, for batch submission.
- On submit, the API creates one `videos` row + one `ingest_jobs` row per URL
  and returns the job ids. Download → sample → pre-label proceeds in the worker
  (see [ingestion-pipeline.md](./ingestion-pipeline.md)).

### Upload
- A file picker (drag-and-drop supported) uploads a local video. Large files are
  uploaded in chunks / streamed. The original filename is shown in the list but
  does **not** determine storage path.

The user does not choose a storage path in the UI — the path is the configured
`storage.root` volume (see [configuration.md](./configuration.md) and
[deployment.md](./deployment.md)). The problem statement's "the app will be
provided a path in which they can store the videos" is satisfied by this
configured root, which the operator sets when deploying.

## The video list

- **Paginated**, `ui.pagination_size` per page (default 24).
- Columns: title, source (youtube/upload), frame count, status, ingest progress,
  created time. Sortable by created time; searchable by title.
- **Live status:** rows update via WebSocket (or poll every
  `ui.poll_interval_ms`) while a job runs, so users see `downloading → sampling
  → labeling → ready` without refreshing.
- **Per-row actions:** retry (if `failed`), delete (removes frames + files +
  rows), and a count breakdown (how many of this video's frames landed in
  training vs. validation).

## Empty / error states

- No videos yet → a friendly prompt to add the first one.
- A failed job shows the `error` text and a **Retry** button (resumes from the
  last completed stage — see [ingestion-pipeline.md](./ingestion-pipeline.md)).

## Endpoints used

See [api.md](./api.md): `POST /videos` (youtube batch), `POST /videos/upload`,
`GET /videos?page=&size=`, `GET /jobs/{id}`, `DELETE /videos/{id}`,
`POST /videos/{id}/retry`.
