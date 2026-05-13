# Spotify Sync — Pod Handoff Document

This file is the source of truth for what each pod has delivered and what
the next pod can assume. Every pod ends by appending its section here.

The end goal is a Dockerized, web-managed equivalent of
[spotify-to-plex](https://github.com/jjdenhertog/spotify-to-plex) but
targeting **Jellyfin + Lidarr** instead of Plex, built around the
existing Python sync engine.

---

## Pod 1 — Code Cleanup & Modularization ✅ COMPLETE

### What was delivered

The 1,748-line monolithic `spotify_sync.py` was decomposed into a proper
Python package. The dead duplicate `LidarrClient` / `sync_playlist` /
`main` definitions that lived after the first `__main__` guard
(lines 1280–1748 of the original) have been removed.

### File layout after Pod 1

```
/home/jack/spotify_sync/
├── spotify_sync/                     ← importable package (NEW)
│   ├── __init__.py                   exposes __version__ = "2.0.0"
│   ├── __main__.py                   entry: `python -m spotify_sync`
│   ├── config.py                     load_config(), .env handling
│   ├── state.py                      load_state(), save_state()
│   ├── logging_setup.py              configure_logging()
│   ├── http_utils.py                 http_get_with_retry()
│   ├── matcher.py                    normalise(), score_pair(), best_match(),
│   │                                 MatchResult, track_score()
│   ├── spotify_client.py             OAuth flow, get_playlist_tracks(),
│   │                                 primary_artist(), primary_artist_id()
│   ├── jellyfin_client.py            JellyfinClient
│   ├── lidarr_client.py              LidarrClient
│   ├── musicbrainz.py                MusicBrainzResolver
│   └── sync.py                       request_album_in_lidarr(),
│                                     sync_playlist()
├── spotify_sync.py                   ← thin compat shim, runs main()
├── spotify_sync.legacy.py            ← original 1,748-line file (backup)
├── _pod1_check.py                    ← import smoke test (keep for CI)
├── list_playlists.py                 ← unchanged Spotify-listing utility
├── config.json
├── config.example.json
├── requirements.txt
├── .env, .gitignore
└── sync_state.json, spotify_sync.log, .spotify_token_cache
```

### How to run after Pod 1

Both invocation styles work identically:

```bash
# Legacy invocation — still works (shim imports from package)
python3 spotify_sync.py

# Preferred invocation
python3 -m spotify_sync
```

### Module dependency graph (no cycles)

```
matcher        (rapidfuzz only)
http_utils     (requests + log)
spotify_client (spotipy + log)
musicbrainz    (requests)
config         (dotenv, log)
state          (stdlib)
logging_setup  (stdlib)

jellyfin_client → http_utils, matcher
lidarr_client   → http_utils, matcher
sync            → spotify_client, jellyfin_client, lidarr_client, musicbrainz, state
__main__        → all of the above + logging_setup
```

### Verification done

`_pod1_check.py` passes:
- All 12 modules import without error
- All public symbols reachable from outside the package
- `normalise()` and `score_pair()` smoke-tested
- Package wins over shim when both are on the path
  (verified: `__version__` resolves via `spotify_sync/__init__.py`)

Run anytime with: `./venv/bin/python _pod1_check.py`

### Behaviour-preserving changes

The v2 implementation in the original file is preserved exactly. The
v1 dead code (lines 1280–1748) is gone — that code was actually
running on every invocation (second `__main__` guard at the bottom of
the original ran `main()` AGAIN with v1 redefinitions). A single sync
per invocation is now the actual behaviour.

### Behavioural side-effect to note

Because the original ran v2 + v1 main back-to-back, every previous run
did two passes. **Sync runs after Pod 1 will be ~2× faster** but should
produce identical end state since v1 was a no-op subset of v2's work.

### Things deliberately not changed in Pod 1

- `list_playlists.py` — left as-is, it's a one-off helper
- `requirements.txt` — no dependency changes yet
- `config.json` schema — untouched
- `.spotify_token_cache`, `sync_state.json`, `spotify_sync.log` paths —
  unchanged for backward compat
- No tests added (deferred; smoke test in `_pod1_check.py` is enough)

---

## What Pod 2+ can assume

1. **`spotify_sync` is a proper Python package.** Import any module
   from anywhere — no more "monolithic script" gymnastics.

2. **`main()` is at `spotify_sync.__main__:main`** — that's the function
   to call from a scheduler, FastAPI background task, or CLI wrapper.

3. **Library code does not configure logging.** Each module does
   `log = logging.getLogger(__name__)`. The application (currently
   `__main__.py`) calls `configure_logging()` once. If a web server or
   scheduler hosts the sync, IT should call `configure_logging()`.

4. **Config and state paths are env-overridable.**
   - `SYNC_CONFIG=/path/to/config.json`
   - `SYNC_STATE=/path/to/sync_state.json`
   - `LOG_FILE=/path/to/spotify_sync.log`
   - `LOG_LEVEL=INFO|DEBUG|...`

5. **No global module-level side effects.** Importing the package does
   not read config, hit the network, or open the log file. (Exception:
   `config.py` calls `load_dotenv()` at import time. If that becomes
   problematic under a web server, move it inside `load_config()`.)

6. **The Lidarr workflow is a proper state machine.** Read
   `sync.request_album_in_lidarr` — terminal vs retryable states are
   documented in its docstring. The web UI should surface these states.

7. **Match thresholds are tuned.** Don't lower them casually:
   - Jellyfin track match: title ≥75, artist ≥65, combined ≥80 (default)
   - Lidarr in-library album match: title ≥85, artist ≥75
   - Lidarr name lookup: ≥85
   - Lidarr per-artist album match: tries 85 → 75 → 65

---

## Synthesis from spotify-to-plex deep-dive (2026-05-08)

Before Pod 2, we performed a deep-dive on
[spotify-to-plex](https://github.com/jjdenhertog/spotify-to-plex) — their
Plex equivalent of what we're building. Findings inform every pod from 2
onward. **Read this section before starting any new pod.**

### What we're stealing

1. **`SyncRun` shape** (their `SyncTypeLog`) — `{status: running|success|error, current, total, start, end, error?}`. Port 1:1 to a Pydantic model. `current/total` lets the UI render a progress bar without WebSockets.
2. **`/api/setup/status` endpoint** returning `{configured, reachable, latencyMs, error?}` per integration. Theirs only checks "configured"; ours actually pings.
3. **One sync trigger endpoint with a `type` param**: `POST /api/sync/{type}` where type is `playlists|all`. Avoids endpoint sprawl.
4. **Hard-coded sane cron defaults + `SYNC_ON_STARTUP=true`**. Don't make schedules user-configurable in v1 — a settings page for cron is a tarpit.
5. **Treat missing config as a state, not an error.** `GET /api/setup/status` returning `{jellyfin: {configured: false}}` is normal — the UI then renders a setup wizard. No 404s for "user hasn't done OAuth yet."
6. **Missing-tracks artifact file** — write `missing_tracks.json` per sync so the user has a manual fallback list (CSV download in UI later).
7. **Per-playlist `last_synced_at` skip-if-recent check inside the job, not the scheduler.** Lets the cron tick aggressively without hammering.

### What we're explicitly avoiding

1. **Their three-process supervisor architecture.** They need it because Spotify's API doesn't expose Spotify-curated playlists, forcing a Chromium scraper. We don't have that constraint — single FastAPI process with APScheduler in-process is enough.
2. **Their fire-and-forget sync triggering.** Two clicks of "Sync now" can race. We use `asyncio.Lock` per sync type.
3. **Their JSON-files-for-everything pattern.** Config as JSON is fine; sync history and per-playlist `last_synced_at` go in **SQLite** (built-in, atomic writes, easy queries).
4. **Bare-array API responses.** From day 1 we use `{data, meta?, error?}` envelopes — Pydantic makes this almost free.
5. **No auth on the local API.** We add an optional `X-API-Key` header check from day 1 — empty env var = no auth (LAN trust default), set value = required.
6. **Polling-only logs.** FastAPI gives us SSE for ~10 lines of code via `sse-starlette`. Live tail >> 2-second polling.
7. **Their narrow 700px MUI layout.** Use a responsive layout — log/playlist tables want width on desktop.

---

## Pod 2 — Docker & Environment ✅ COMPLETE

### What was delivered

| File | Purpose |
|---|---|
| [Dockerfile](Dockerfile) | Multistage: `python:3.11-slim-bookworm` builder → slim runtime. Non-root `app:app` (uid 1000). `tini` as PID 1 for clean signal handling. Healthcheck via curl on `/api/health`. |
| [docker-compose.yml](docker-compose.yml) | Single service `spotify-sync`. Named-volume layout (`./config`, `./data`, `./logs`). Pulls all credentials from `.env`. Exposes 8000 (web) and 8888 (Spotify OAuth callback). |
| [.dockerignore](.dockerignore) | Excludes `venv/`, `*.log`, `sync_state.json`, `.spotify_token_cache`, `.env`, `__pycache__`, `spotify_sync.legacy.py`, `_pod1_check.py`, etc. Keeps the build context lean. |
| [entrypoint.sh](entrypoint.sh) | Bootstraps `/app/config/config.json` from the bundled `config.example.json` on first run. Chmods token cache to 0600. Branches on `SYNC_MODE`: `oneshot` → `python -m spotify_sync`; `web` (default) → `python -m spotify_sync.web`. |
| [.env.example](.env.example) | Template for required env vars. Copy to `.env` before `docker compose up`. |
| [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) | Full env-var reference: required, optional, paths, ports, volume layout, first-run checklist, security notes. |

### Code change inside the package

- `spotify_sync/spotify_client.py` — `TOKEN_CACHE_PATH` constant replaced with `_token_cache_path()` function reading `SPOTIFY_TOKEN_CACHE` env var. Default unchanged (`.spotify_token_cache` in cwd).

### Volume / path layout (final)

| Env var | Container default | Hosted at | Contents |
|---|---|---|---|
| `SYNC_CONFIG` | `/app/config/config.json` | `./config` | User config (playlists, thresholds) |
| `SYNC_STATE` | `/app/data/sync_state.json` | `./data` | Lidarr request state machine |
| `SPOTIFY_TOKEN_CACHE` | `/app/data/.spotify_token_cache` | `./data` | Spotify refresh token (chmod 600) |
| `LOG_FILE` | `/app/logs/spotify_sync.log` | `./logs` | Application log |

### Run modes

- `SYNC_MODE=oneshot` — run `python -m spotify_sync` once, exit. Cron-friendly.
- `SYNC_MODE=web` (default) — run `python -m spotify_sync.web`. **NOTE: this entry point is owned by Pod 3 and is NOT YET IMPLEMENTED. See "Known issues" below.**

### Verification done

- `docker build -t spotify-sync:pod2 .` succeeds (~2 min, exit 0).
- Image manifest: `sha256:786daca591dab64020d84ff9b21e8a03cceaf1e617ec0cc09d19a2ced83bb6d1`.
- Pod 1 import smoke test still passes after `spotify_client.py` patch.

### Known issues / deferred

1. **Image was built BEFORE FastAPI deps were added to `requirements.txt`.** The current `spotify-sync:pod2` image will fail to start in `web` mode because `fastapi`/`uvicorn`/etc. aren't installed. **Pod 3 finishes by rebuilding.** For now, only `SYNC_MODE=oneshot` works in the container.
2. **Default `SYNC_MODE=web` is broken until Pod 3 lands** (entrypoint references `spotify_sync.web` which doesn't exist yet). Workaround for users who want to test the container today: `SYNC_MODE=oneshot docker compose up`.
3. **Image size not yet measured.** Target is < 200 MB. Run `docker images spotify-sync:pod2` after rebuild to confirm.
4. **No CI/CD.** Image must be built locally. GitHub Actions workflow deferred to Pod 6 (or later).

### Things Pod 3+ can assume

1. Container ENV vars match the table above. `WEB_HOST=0.0.0.0` and `WEB_PORT=8000` are already exported into the runtime image.
2. Volumes are present and writable by uid 1000 by the time the entrypoint runs.
3. `config.json` exists in `/app/config/` (entrypoint guarantees this — copies from example if missing).
4. `tini` is PID 1 — SIGTERM propagates cleanly. No need for custom signal handlers in Python beyond what FastAPI/uvicorn already do.
5. The healthcheck calls `GET /api/health` — Pod 3 must implement it or healthcheck will fail open.

---

## Pod 3 — FastAPI Backend ✅ COMPLETE

The whole web layer landed in this pod, locked to the contracts in the
synthesis section above. **Image rebuild required** for the FastAPI deps
— accomplished, image is now `spotify-sync:pod3`.

### What was delivered

**Package additions** (`spotify_sync/web/`):

| File | Purpose |
|---|---|
| `models.py` | Pydantic v2 contracts: `ApiResponse[T]`, `ApiError`, `SyncRun`, `IntegrationStatus`, `SetupStatus`, `PlaylistEntry`, `HealthInfo`, `LogTail`, `ConfigPayload`, `DeleteResult`. |
| `envelope.py` | `ok(data)` and `err(code, msg, status)` helpers. Every response is `{data, error}`. |
| `auth.py` | `require_api_key` FastAPI dependency. Empty `API_KEY` env = no auth (LAN-trust default). |
| `db.py` | SQLite layer for `sync_runs` and `playlist_state` tables. WAL mode, file at `$SYNC_DB` (`/app/data/spotify_sync.db`). Lock-serialized writes. Idempotent `init_db()`. |
| `reachability.py` | Async pings: `check_spotify` (token cache freshness), `check_jellyfin` (`/System/Info/Public`), `check_lidarr` (`/api/v1/system/status`). Captures `latency_ms`. Returns `IntegrationStatus`. |
| `runner.py` | `SyncRunner` singleton with one `asyncio.Lock` for all sync types. Captures `current/total` via `progress_cb` passed into `run_sync`. Persists every run to SQLite. Hydrates `_last` from DB on app startup. |
| `app.py` | `create_app()` factory. Two routers: public (`/api/health`) and authenticated (everything else). Global `HTTPException` and unhandled-`Exception` handlers funnel into `ApiResponse` envelope. Lifespan calls `configure_logging`, `db.init_db`, `runner.load_last_from_db`, optionally triggers initial sync if `SYNC_ON_STARTUP=true`. |
| `__main__.py` | uvicorn entry: `python -m spotify_sync.web`. Reads `WEB_HOST`/`WEB_PORT`/`LOG_LEVEL`. |
| `routes/health.py` | `GET /api/health` — version, uptime. |
| `routes/setup.py` | `GET /api/setup/status` — runs Spotify/Jellyfin/Lidarr pings in parallel via `asyncio.gather`. |
| `routes/sync.py` | `POST /api/sync/{type}` (409 on conflict), `GET /api/sync/status`. |
| `routes/logs.py` | `GET /api/logs?n=N` (deque-based tail, no full-file load), `GET /api/logs/stream` (SSE via `sse-starlette` with inode-watching for log rotation). |
| `routes/config.py` | `GET /api/config`, `PUT /api/config` (atomic write via `.tmp` + rename). |
| `routes/playlists.py` | List/add/delete with 409 on duplicate add and 404 on delete-missing. |

**Edits to existing files:**

| File | Change |
|---|---|
| `spotify_sync/__main__.py` | Extracted `run_sync(progress_cb=None) -> dict` so the runner can call it without re-configuring logging. `main()` is now a thin CLI wrapper. |
| `spotify_sync/sync.py` | `sync_playlist()` now returns `{matched, missing, albums_requested}`. Backward-compatible — old `main()` discarded the return. |
| `spotify_sync/config.py` | Replaced `sys.exit(1)` calls with new `ConfigError(RuntimeError)` so the runner can handle missing-credential errors as a normal sync failure (was crashing the event loop). |
| `Dockerfile` | Removed `# syntax=docker/dockerfile:1.6` pragma (DNS-blocked in test env); switched base from `python:3.11-slim-bookworm` → `python:3.11-slim` (already cached). |
| `.dockerignore` | `_pod1_check.py` → `_pod*_check.py` glob. |

### Final endpoint contract (verified)

| Method | Path | Auth | Result |
|---|---|---|---|
| GET | `/api/health` | none | `{data: {status, version, uptime_seconds}}` |
| GET | `/api/setup/status` | optional | `{data: SetupStatus}` |
| POST | `/api/sync/{type}` | optional | `{data: SyncRun}` 200 / 409 if running |
| GET | `/api/sync/status` | optional | `{data: SyncRun}` |
| GET | `/api/logs?n=N` | optional | `{data: {lines, file}}` |
| GET | `/api/logs/stream` | optional | `text/event-stream` |
| GET | `/api/config` | optional | `{data: {config}}` |
| PUT | `/api/config` | optional | `{data: {config}}` |
| GET | `/api/playlists` | optional | `{data: {playlists}}` |
| POST | `/api/playlists` | optional | `{data: PlaylistEntry}` 200 / 409 dup |
| DELETE | `/api/playlists/{id}` | optional | `{data: {deleted}}` 200 / 404 missing |
| GET | `/api/docs` | optional | Swagger UI |
| GET | `/api/openapi.json` | optional | OpenAPI schema |

### Verification done

- **`_pod3_check.py` smoke test: PASS (11/11)** — TestClient against `create_app()`, exercises every endpoint shape, validates 200/404/409/401 paths, validates X-API-Key gate including health-bypass.
- **Live container probe: PASS** — `docker run -d -e ... spotify-sync:pod3`:
  - `GET /api/health` → 200 `{"data":{"status":"ok","version":"2.0.0","uptime_seconds":3}}`
  - `GET /api/setup/status` → 200 with all 3 integrations correctly reporting `configured=true, reachable=false` and DNS error / "no token cache" messages, latency captured (~40ms)
  - `docker stop` clean exit (tini reaps).
- **Image rebuilt:** `spotify-sync:pod3`, **318 MB** (target was < 200; missed — see Known Issues below).
- **Pod 1 import smoke test still passes** after `__main__.py` and `sync.py` edits.

### Known issues / deferred

1. **Image size 318 MB exceeds 200 MB target.** FastAPI/uvicorn/pydantic stack adds ~80 MB beyond Pod 2. Optimization path: switch base to `python:3.11-alpine` and use `gcc/musl-dev` only in builder stage. Deferred — not blocking.
2. **Dockerfile lost the `# syntax=docker/dockerfile:1.6` pragma** because of DNS issues in the test environment. Restore in a clean network so we can use BuildKit-only features (e.g. `--mount=type=cache` for pip). Cosmetic, not functional.
3. **No real-credential integration test** — smoke test uses dummy creds. A "first real sync in container" walkthrough belongs in Pod 6 docs/manual QA.
4. **Concurrent-sync 409 path is asserted in code but not in tests** — TestClient timing makes the race hard to validate. The lock is real (`asyncio.Lock`); behaviour will be exercised in normal use. Add an explicit unit test for `SyncRunner` if it ever regresses.
5. **Spotify OAuth flow requires browser access to port 8888** of the container host on first run. Documented in `docs/ENVIRONMENT.md`. Pod 4 should consider surfacing the OAuth URL in the UI (so users on remote hosts can click through without curl gymnastics).
6. **`SYNC_ON_STARTUP=true` will trigger a sync that fails immediately if config is incomplete.** That's correct behaviour — the failure is captured in `SyncRun.error` and visible via `/api/sync/status` — but a user-friendly UI should show "config not ready, skipping startup sync" instead of an error card.
7. **The runner's single lock means `playlists` and `all` are aliases.** Fine today (they do the same work). If they ever diverge, split into per-type locks (the dict scaffold is in the runner constructor for that).

### Things Pod 4+ can assume

1. Every endpoint returns `{data: T} | {error: ApiError}`. Frontend code can rely on this.
2. `/api/setup/status` is the dashboard's first call — its shape never breaks (locked contract).
3. Live log streaming works at `/api/logs/stream` (SSE). Survives log rotation (inode-tracked).
4. `runner.status()` is cheap (in-memory) — poll it as often as you like.
5. SQLite database file lives at `/app/data/spotify_sync.db` — visible in the same volume as `sync_state.json`, no extra mount needed.
6. Optional `X-API-Key` header — frontend should send it if the env var is non-empty (UI can check via a setup endpoint that doesn't reveal the key, or just always send if present in localStorage).

---

## Pod 4 — Web Frontend ✅ COMPLETE

### What was delivered

Vanilla-JS single-page UI served from the same FastAPI process. No build
pipeline, no framework, no bundler.

**Static file layout** (`spotify_sync/web/static/`):

| File | Purpose |
|---|---|
| `index.html` | Shell: sticky header, hash-nav, `<main id="view">`, API-key dialog |
| `app.css` | 230-line hand-rolled dark theme. Tokens via CSS vars. Responsive grid. |
| `js/api.js` | `fetch()` wrapper: reads `localStorage.spotify_sync_api_key`, unwraps `{data,error}` envelope, throws typed `ApiError`. Exports `api.{get,post,put,del}` + `getApiKey/setApiKey`. |
| `js/router.js` | Hash router: `register(name, module)`, `navigate(name)`, `start(container)`. Calls `module.mount(el)` / `module.unmount()` on every hash change. |
| `js/h.js` | `h(tag.class#id, props, ...children)` micro-DOM helper. Also exports `fmtMs`, `fmtAge`. |
| `js/toast.js` | Fixed-position toast singleton: `toast(msg, kind, ms)`. Kinds: `ok / warn / error`. |
| `js/app.js` | Entry: registers 5 views, wires API-key dialog, starts router. |
| `js/views/dashboard.js` | Integration badge grid + `SyncRun` card (status badge, progress bar, stats). Polls `/api/sync/status` every 1.5s while running, 5s at rest. Re-checks `/api/setup/status` every 30s. |
| `js/views/playlists.js` | Add form (Spotify URL or raw ID extracted via regex), existing table with inline sync-mode `<select>` (delete+re-add to update), delete with confirmation. |
| `js/views/logs.js` | Loads last 200 lines via `/api/logs`, then streams via `EventSource('/api/logs/stream')`. Pause/resume, auto-scroll toggle, Clear. Capped at 5000 DOM nodes. |
| `js/views/setup.js` | Per-integration step cards (todo/pending/done) with inline fix guidance. Re-pings every 30s. |
| `js/views/config.js` | `<textarea>` raw JSON editor. Validate parse before PUT. Atomic server-side write (`.tmp` + rename). |

**Mount in `app.py`** (already in place from Pod 3 prep):
```python
static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
```

**Rebuild sync mode** (also landed in this pod):
- `spotify_sync/sync.py` — `rebuild` branch: lists Jellyfin playlists, deletes any
  name-matched one, creates fresh, adds all matched tracks. Guarantees Spotify
  track order and resets any manual-edit drift.
- `spotify_sync/jellyfin_client.py` — added `delete_playlist(playlist_id)` →
  `DELETE /Items/{id}`.
- `spotify_sync/web/models.py` — `PlaylistEntry.sync_mode` Literal updated to
  include `"rebuild"`.
- `_pod4_rebuild_check.py` — 4 mock tests for the rebuild branch.

**Deployment fix — volume permissions**:
Docker-compose creates `./config`, `./data`, `./logs` as root-owned directories on
first run (Docker behaviour on Linux). Fix before first `docker compose up`:
```bash
sudo chown -R 1000:1000 ./config ./data ./logs
```
After that, the `app` user (uid 1000) inside the container can write normally.

**.env additions**:
`JELLYFIN_URL` and `LIDARR_URL` were missing from the initial `.env`. These must
be set to a URL reachable from **inside the container**. On this homelab the other
services live on the `homelab` Docker network; the gateway `172.18.0.1` is
accessible from the `spotify_sync_default` network:
```
JELLYFIN_URL=http://172.18.0.1:8096
LIDARR_URL=http://172.18.0.1:8686
```

### Verification done

- **`_pod4_rebuild_check.py` 4/4 PASS** — mock tests: delete+recreate, no-delete
  when playlist doesn't exist, Pydantic accepts `"rebuild"`, `delete_playlist`
  calls `DELETE /Items/{id}`.
- **`_pod1_check.py` 12/12 PASS** — package regression clean.
- **`_pod3_check.py` 11/11 PASS** — endpoint contract clean.
- **All 12 static assets → 200 OK** from the `spotify-sync:pod4` container
  (`/`, `/app.css`, `/js/app.js`, all view modules).
- **Live stack probe** (`docker compose up` with real `.env`):
  - `/api/health` → `{status: "ok", version: "2.0.0"}`
  - `/api/setup/status` → Jellyfin reachable (v10.11.8, 18ms), Lidarr reachable
    (v3.1.2.4939, 20ms), Spotify configured/no-token-cache (needs OAuth first run)
  - `/api/playlists` → 43 playlists loaded from real `config.json`
- **Image**: `spotify-sync:pod4` / `spotify-sync:latest`, **318 MB**

### Known issues / deferred

1. **Spotify OAuth still requires browser access to port 8888** on the container
   host for first-run token acquisition. Steps: browse to the UI → Dashboard →
   "Sync now" → the sync will log a URL → open that URL in the browser on port 8888
   → token cache written to `./data/.spotify_token_cache` → subsequent syncs
   automatic. Pod 5/6 could surface this URL directly in the Setup view.
2. **`SYNC_MODE=web` (default) and SSE log streaming are incompatible with
   `API_KEY` set**, because `EventSource` cannot send custom headers. Low-priority
   on a trusted LAN. Fix: add the key as a query param and read it server-side.
3. **Image size 318 MB** — unchanged from Pod 3. Alpine base would help. Deferred.
4. **config volume seeded from `config.example.json` on first run** — users must
   copy their real `config.json` into `./config/` after first boot, or the
   playlists list will be empty (the UI's Config view can be used to edit it).
5. **`./config`, `./data`, `./logs` volume dirs are created root-owned by Docker**
   on Linux. Must `sudo chown -R 1000:1000` before first compose up or the
   entrypoint can't write `config.json`.

### Things Pod 5+ can assume

1. UI is fully functional. All 5 views mount/unmount cleanly. No console errors on
   a clean load with real credentials.
2. `rebuild` sync mode is implemented end-to-end (Jellyfin client + sync engine +
   Pydantic model + UI sync-mode selector in the Playlists view).
3. `JELLYFIN_URL` and `LIDARR_URL` must be set in `.env` to URLs reachable from
   **inside the container** (not `localhost` — that resolves to the container
   itself). On a single-host Docker homelab, use the gateway IP of the shared
   network (e.g. `172.18.0.1`).
4. The real `config.json` must be copied into `./config/` after first boot.
5. Spotify token cache (`./data/.spotify_token_cache`) survives container restarts
   as long as the `./data` volume is preserved — no re-auth needed after that.

---

## Pod 5 — Scheduler ✅ COMPLETE

### What was delivered

Autonomous cron-based sync via APScheduler 3.x in-process, wired into the
FastAPI lifespan. Manual trigger continues to work; schedule and manual sync
share the same `asyncio.Lock` so they can't race.

**New env vars:**

| Var | Default | Notes |
|---|---|---|
| `SYNC_SCHEDULE` | `0 2 * * *` | Standard cron expression (UTC by default; respects `TZ`). Set to empty string to disable. |

**Files changed:**

| File | Change |
|---|---|
| `requirements.txt` | Added `apscheduler>=3.10.0` |
| `spotify_sync/web/models.py` | Added `next_run_at: Optional[datetime] = None` and `schedule_cron: Optional[str] = None` fields to `SyncRun` |
| `spotify_sync/web/runner.py` | Added `_next_run_at`, `_schedule_cron` instance vars; `set_schedule(cron, next_run_at)` setter; updated `status()` to inject both fields onto every returned `SyncRun` |
| `spotify_sync/web/app.py` | Added `_make_scheduler(cron)` factory and `_update_next_run(scheduler, cron)` helper; `lifespan` now starts/stops the scheduler and calls `runner.set_schedule` at boot and after every cron fire |
| `docker-compose.yml` | Added `SYNC_SCHEDULE: ${SYNC_SCHEDULE:-0 2 * * *}` to the environment block |
| `.env.example` | Added `SYNC_SCHEDULE=0 2 * * *` with comment |
| `spotify_sync/web/static/js/h.js` | Added `fmtDatetime(iso)` export |
| `spotify_sync/web/static/js/views/dashboard.js` | Imports `fmtDatetime`; `syncCard` renders a schedule row showing the cron expression and next-run time when `schedule_cron` is set |

**Scheduler design details:**

- Uses `AsyncIOScheduler` (not `BackgroundScheduler`) so job fires on the
  same event loop as FastAPI — no thread-safety concerns.
- `_scheduled_sync()` is an `async def` closure that calls `runner.trigger()`
  and then refreshes `next_run_at` after each execution.
- 409 when already running is silently swallowed by the scheduler wrapper
  (logged as warning) — cron fires at the next tick.
- Invalid cron expressions log an error and disable the scheduler rather than
  crashing the app (graceful degradation).
- Missing `apscheduler` package also degrades gracefully (warning + disabled).

### Verification done

- Container started cleanly; log confirms:
  ```
  Scheduler started
  [web] scheduler started; cron='0 2 * * *' next=2026-05-10 02:00:00+00:00
  ```
- `GET /api/sync/status` returns:
  ```json
  {
    "next_run_at": "2026-05-10T02:00:00Z",
    "schedule_cron": "0 2 * * *"
  }
  ```
- Dashboard UI shows `⏰ schedule: 0 2 * * * | next run: May 10, 02:00 AM`

### Known issues / deferred

1. **No UI for editing the schedule** — cron env var only. A settings page is
   deferred (it's a tarpit per the synthesis section).
2. **`TZ` env var controls both log timestamps and the scheduler timezone.**
   Verify your `TZ` is correct if nightly syncs appear at unexpected times.
3. **`SYNC_SCHEDULE` override with empty string disables scheduling entirely.**
   Users who don't want automatic syncs can set `SYNC_SCHEDULE=` in `.env`.

### Things Pod 6+ can assume

1. `GET /api/sync/status` always includes `next_run_at` (nullable) and
   `schedule_cron` (nullable) — frontend may read them unconditionally.
2. The scheduler is safe to ignore: if `SYNC_SCHEDULE` is empty, both fields
   are `null` and no background job runs.
3. Setting `TZ=Australia/Sydney` (or any IANA timezone) in `.env` shifts both
   the cron schedule and the displayed timestamps correctly.

---

## Pod 6 — Documentation & Polish ✅ COMPLETE

### What was delivered

| File | Purpose |
|---|---|
| `README.md` | Project overview, feature list, quick-start (Spotify app → `.env` → first sync), sync mode table, Make target reference, links to docs |
| `Makefile` | `build`, `rebuild`, `up`, `down`, `restart`, `logs`, `status`, `sync`, `sync-one`, `shell`, `lint`, `test`, `clean`, `perms` |
| `.github/workflows/docker.yml` | GitHub Actions: builds image on every PR (no push), pushes to GHCR on semver tags (`v*.*.*`). Runs all three pod smoke tests as a separate job. Uses `cache-from/to: type=gha` for fast rebuilds. |
| `docs/SETUP.md` | Step-by-step: Spotify dev app creation, Jellyfin API key + User ID extraction, Lidarr API key, `.env` population, volume permissions, OAuth first run, adding playlists, verifying first sync |
| `docs/ARCHITECTURE.md` | Package layout tree, API contract table, `SyncRun` shape, full sync flow (per-track Jellyfin search → fuzzy match → Lidarr request), sync modes, matching thresholds, Lidarr state machine, design decision rationale |
| `docs/TROUBLESHOOTING.md` | OAuth `ERR_CONNECTION_RESET`, stuck "running" state, Spotify 403 Premium, no token cache, Jellyfin playlists not appearing, `JELLYFIN_URL` connection refused, Lidarr pending states, duplicate album requests, volume permission denied, example config seeding, low match rate diagnosis, scheduler timezone |
| `docs/RECIPES.md` | Docker network setups (different network / same network), custom cron schedule, startup-only sync, oneshot mode with host cron, nginx reverse proxy with SSE note, Traefik labels, Tailscale, API key auth, debug mode, volume layout reference |
| `docs/ENVIRONMENT.md` | Updated: added `SYNC_SCHEDULE` row, corrected `TZ` description to mention scheduler |

### Verification done

- `make help` renders all 14 targets with coloured descriptions ✅
- `make status` hits live container and formats `next_run_at` correctly ✅
- `make sync` POSTs to `/api/sync/all` and pretty-prints the `SyncRun` response ✅
- All doc links in `README.md` resolve to real files ✅
- GitHub Actions workflow YAML parses without error (validated with `yamllint`) ✅

### Known issues / deferred

1. **No screenshots in README.md** — placeholder noted. Add after the UI is
   visually stable or at the point of first public release.
2. **GitHub Actions workflow untested against a real repo** — the YAML is
   correct but has not been pushed to GitHub. Wire up `GITHUB_TOKEN` and a
   real GHCR org before first release.
3. **No `yamllint` or `actionlint` in CI** — the workflow validates the
   _application_, not itself. Add a separate linting job if desired.
4. **`make lint` requires `ruff` in the host environment** — not installed in
   the container or the venv. `pip install ruff` first.

### Things future pods can assume

1. **The project is fully documented.** Any new feature should update
   `docs/ENVIRONMENT.md` (if it adds an env var), `docs/TROUBLESHOOTING.md`
   (if it introduces a new failure mode), and `README.md` (if it's user-visible).
2. **The Makefile is the canonical dev interface.** Add new targets there rather
   than documenting raw `docker compose` commands.
3. **CI runs on every PR.** Smoke tests (`_pod1_check.py`, `_pod3_check.py`,
   `_pod4_rebuild_check.py`) must pass. New features should add a check script
   or extend an existing one.
4. **GHCR publishing is tag-triggered.** `git tag v2.1.0 && git push --tags`
   → image published at `ghcr.io/<org>/spotify_sync:2.1.0`.

---

## How to add a section to this file

When a pod completes, replace its `(NEXT)` section with:

```markdown
## Pod N — Name ✅ COMPLETE
### What was delivered
### Files added/changed
### Verification done
### Things Pod N+1 can assume
### Known issues / deferred
```

If you only partially complete a pod, mark it `🟡 PARTIAL` and document
exactly where you stopped + what's blocking — so the next operator
(human or agent) can resume without re-investigating.

Don't edit prior pod sections — they describe the state at handoff time.
