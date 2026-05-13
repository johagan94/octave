# Architecture

How `spotify_sync` is structured and why.

---

## Overview

```
Browser
  │  HTTP
  ▼
FastAPI (port 8000)
  ├── /api/*          JSON REST API
  ├── /api/logs/stream  SSE (live log tail)
  └── /              Static SPA (vanilla JS)

FastAPI process
  ├── SyncRunner      asyncio.Lock + progress tracking
  ├── APScheduler     in-process cron (AsyncIOScheduler)
  └── SQLite (WAL)    sync run history

SyncRunner.run_sync()   (runs on a worker thread via asyncio.to_thread)
  ├── SpotifyClient   spotipy OAuth + playlist pagination
  ├── JellyfinClient  library search + playlist CRUD
  ├── LidarrClient    album lookup + request
  └── MusicBrainzResolver  MBID fallback for unknown artists
```

Everything runs in a **single process, single container**. No message queue,
no supervisor, no sidecar. The asyncio event loop stays responsive during
a sync because the blocking sync work is dispatched via `asyncio.to_thread`.

---

## Package layout

```
spotify_sync/                   importable package
├── __init__.py                 __version__ = "2.0.0"
├── __main__.py                 CLI entry: python -m spotify_sync
│                               Exports run_sync(progress_cb, playlist_ids) → dict
├── config.py                   load_config() — reads SYNC_CONFIG env var
├── state.py                    load_state(), save_state() — Lidarr state machine
├── logging_setup.py            configure_logging() — file + stderr handlers
├── http_utils.py               http_get_with_retry() — exponential backoff
├── matcher.py                  normalise(), score_pair(), best_match()
│                               RapidFuzz title+artist scoring with tuned thresholds
├── spotify_client.py           SpotifyOAuth flow, get_playlist_tracks()
├── jellyfin_client.py          JellyfinClient — search, playlist CRUD
├── lidarr_client.py            LidarrClient — album lookup, request
├── musicbrainz.py              MusicBrainzResolver — MBID lookup via MusicBrainz API
├── sync.py                     sync_playlist() — orchestrates one playlist
│                               request_album_in_lidarr() — state machine
└── web/                        FastAPI layer (web-mode only)
    ├── __main__.py             uvicorn entry: python -m spotify_sync.web
    ├── app.py                  create_app() factory, lifespan, scheduler wiring
    ├── models.py               Pydantic v2 contracts (locked — see below)
    ├── envelope.py             ok(data), err(code, msg, status) helpers
    ├── auth.py                 require_api_key FastAPI dependency
    ├── db.py                   SQLite layer — sync_runs table, WAL mode
    ├── runner.py               SyncRunner singleton — asyncio.Lock, progress, history
    ├── reachability.py         async pings for Spotify / Jellyfin / Lidarr
    ├── routes/
    │   ├── health.py           GET /api/health
    │   ├── setup.py            GET /api/setup/status
    │   ├── sync.py             POST /api/sync/{type}, GET /api/sync/status
    │   ├── logs.py             GET /api/logs, GET /api/logs/stream (SSE)
    │   ├── config.py           GET/PUT /api/config
    │   └── playlists.py        GET/POST/DELETE /api/playlists/{id}
    └── static/
        ├── index.html          SPA shell — sticky header, hash router
        ├── app.css             Hand-rolled dark theme (CSS custom properties)
        └── js/
            ├── app.js          Entry: registers views, wires API-key dialog
            ├── api.js          fetch() wrapper — reads localStorage key, unwraps envelope
            ├── router.js       Hash router — mount/unmount views on hash change
            ├── h.js            h(tag, props, ...children) micro-DOM helper
            ├── toast.js        Fixed-position toast singleton
            └── views/
                ├── dashboard.js   Integration cards, sync card, progress bar
                ├── playlists.js   Add form, table, bulk edit, inline mode select
                ├── logs.js        Log tail + SSE stream, pause/resume
                ├── setup.js       Step-by-step integration health cards
                └── config.js      Raw JSON editor with client-side validation
```

---

## API contract

Every response uses the `{data, error}` envelope:

```json
{ "data": <T>,   "error": null  }   // success
{ "data": null,  "error": {"code": "...", "message": "..."} }  // failure
```

Never break the envelope shape. The frontend relies on it unconditionally.

### Endpoint summary

| Method | Path | Auth | Shape |
|---|---|---|---|
| GET | `/api/health` | none | `HealthInfo` |
| GET | `/api/setup/status` | optional | `SetupStatus` |
| POST | `/api/sync/{type}` | optional | `SyncRun` (409 if running) |
| GET | `/api/sync/status` | optional | `SyncRun` |
| GET | `/api/logs?n=N` | optional | `LogTail` |
| GET | `/api/logs/stream` | optional | `text/event-stream` |
| GET | `/api/config` | optional | `{config: dict}` |
| PUT | `/api/config` | optional | `{config: dict}` |
| GET | `/api/playlists` | optional | `{playlists: [PlaylistEntry]}` |
| POST | `/api/playlists` | optional | `PlaylistEntry` (409 on dup) |
| DELETE | `/api/playlists/{id}` | optional | `{deleted: bool}` |

### SyncRun shape

```json
{
  "type": "all",
  "status": "running | success | error | idle",
  "started_at": "2026-05-10T02:00:00Z",
  "finished_at": null,
  "current": 12,
  "total": 43,
  "matched": 1840,
  "missing": 47,
  "albums_requested": 12,
  "error": null,
  "next_run_at": "2026-05-11T02:00:00Z",
  "schedule_cron": "0 2 * * *"
}
```

---

## Sync flow

For each configured playlist:

```
1. SpotifyClient.get_playlist_tracks(playlist_id)
   → List of {title, artist, duration_ms, spotify_id}

2. For each Spotify track:
   a. JellyfinClient.search(title, artist)
      → candidates list
   b. matcher.best_match(spotify_track, candidates)
      → MatchResult{item, score, reason} or None
      Threshold: title≥75, artist≥65, combined≥80

3. Matched tracks → JellyfinClient.add_to_playlist(playlist_id, item_ids)
   (sync_mode controls delete/rebuild behaviour first)

4. Unmatched tracks:
   a. LidarrClient.search_artist(artist_name)
      → Lidarr artist record or None
   b. If not found → MusicBrainzResolver.resolve(artist_name)
      → MBID → LidarrClient.add_artist(mbid)
   c. LidarrClient.search_album(artist, album_title)
      → request_album_in_lidarr(state_machine)

5. Returns {matched, missing, albums_requested}
```

---

## Sync modes

| Mode | Pre-sync action | Post-sync |
|---|---|---|
| `add_only` | None | Add matched tracks not already in playlist |
| `full_sync` | None | Add new tracks, remove tracks no longer on Spotify |
| `rebuild` | Delete playlist in Jellyfin | Create fresh, add all matched tracks in Spotify order |

---

## Matching

Track matching is pure fuzzy string comparison — no audio fingerprinting.

```python
score = 0.6 * token_sort_ratio(title_a, title_b)
      + 0.4 * token_sort_ratio(artist_a, artist_b)
```

Thresholds (tuned against a real 43-playlist library):

| Match type | Title | Artist | Combined |
|---|---|---|---|
| Jellyfin track | ≥ 75 | ≥ 65 | ≥ 80 |
| Lidarr album (in-library) | ≥ 85 | ≥ 75 | — |
| Lidarr album (name lookup) | ≥ 85 | — | — |
| Lidarr per-artist album | tries 85 → 75 → 65 | — | — |

Lower thresholds increase false positives. Don't lower them without testing
on a representative sample of your library.

---

## State machine (Lidarr requests)

Each album request has a state stored in `sync_state.json`:

```
pending        → requested (after Lidarr API call)
requested      → monitored (after Lidarr confirms it)
monitored      → (done — checked on next sync)
error          → retried on next sync (up to MAX_RETRIES)
```

Terminal states (`monitored`, `max_retries_exceeded`) are not retried.

---

## Design decisions

### Why vanilla JS, no framework?

The UI surface is small (~5 views). A build pipeline (Webpack/Vite/esbuild)
adds operational complexity with no clear benefit for a single-container
homelab tool. The `h()` helper and hash router cover all needs.

### Why SQLite, not flat JSON?

Sync run history needs atomic writes and easy queries. SQLite with WAL mode
gives both at zero operational cost. Flat JSON risks corruption on a crash
mid-write.

### Why APScheduler in-process, not a cron container?

The spotify-to-plex reference project uses a three-process supervisor. We
don't need a Chromium scraper, so a single FastAPI process with an in-process
`AsyncIOScheduler` is sufficient. It has direct access to the `SyncRunner`
state machine and the asyncio event loop.

### Why asyncio.Lock rather than a per-type lock dict?

`playlists` and `all` do the same work today. A per-type lock dict would be
premature — add it when/if they diverge.

### Why not expose `/api/logs/stream` to key-authenticated clients?

`EventSource` cannot send custom headers. A proper fix is to accept the API
key as a query parameter on that endpoint. Deferred — the LAN-trust default
means the endpoint is fine without auth in a typical homelab setup.
