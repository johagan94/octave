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

## Pod 4 — Web Frontend (NEXT)

### Goal
A usable web UI served from the same FastAPI process — single page
load, no build pipeline, dependencies vendored.

### Deliverables expected

```
spotify_sync/web/static/
├── index.html              shell (loads JS modules, mounts views)
├── app.css                 hand-rolled or vendored Pico/Simple.css
├── js/
│   ├── api.js              fetch() wrapper handling envelope + X-API-Key
│   ├── router.js           hash-based router (#/dashboard, #/playlists, ...)
│   ├── views/
│   │   ├── setup.js        wizard if /api/setup/status reports any not-configured
│   │   ├── dashboard.js    SyncRun card, integration badges, "Sync now"
│   │   ├── playlists.js    table + add/delete forms
│   │   ├── logs.js         EventSource tail + history fetch
│   │   └── settings.js     env display + per-integration test buttons
│   └── components/
│       ├── status_badge.js (configured/reachable/latency badge)
│       └── progress_bar.js (current/total bar)
└── vendor/
    └── pico.min.css        OR equivalent — keep it tiny
```

`app.py` mount one line:
```python
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                            html=True), name="static")
```

### Implementation guidance (lessons from Pod 3)

1. **Don't build a SPA framework.** Hash routing + 5 view modules is enough. The whole UI should be < 1000 LOC of JS.
2. **Always go through `/api/setup/status` on first load** — render the setup wizard if any integration is not configured, otherwise jump to dashboard. spotify-to-plex got this pattern right.
3. **`api.js` reads `localStorage.api_key`** and adds `X-API-Key` header if present. Login flow: a settings-page input that writes to localStorage. No "real" auth UI.
4. **EventSource just works** — `new EventSource('/api/logs/stream')` with auto-reconnect on close. Wire up a Pause button by closing/reopening.
5. **Progress bar reads `SyncRun.current / SyncRun.total`** — poll `/api/sync/status` every 2s while `status === "running"`. Stop polling otherwise.
6. **Use the OpenAPI schema for free** — Swagger UI is at `/api/docs` for developers, but the human UI doesn't need it.

### Don't

- Don't copy spotify-to-plex's narrow 700px MUI layout — use full-width responsive tables.
- Don't introduce React/Vue/Svelte. The Pod 3 endpoints are designed to be served raw; a framework adds bundle size for no functional gain.
- Don't put the OAuth flow inside an iframe — Spotify forbids it. Show the URL with a "click to authorize" button and poll `/api/setup/status` until `spotify.reachable === true`.
- Don't store the API key anywhere except localStorage. No cookies, no server-side session.

### Verification expected

- Load `/` in browser → setup wizard if not configured, dashboard otherwise
- All views render without console errors with Lighthouse > 90 on accessibility
- Live log stream survives a 30s idle window without dropping
- `Sync now` triggers a sync, progress bar advances as `current/total` updates

---

## Pod 5 — Scheduler (revised)

Move from "manual trigger only" (Pod 3) to autonomous. APScheduler
in-process inside the FastAPI app — `BackgroundScheduler` started in
the FastAPI lifespan event.

```python
@asynccontextmanager
async def lifespan(app):
    scheduler = AsyncIOScheduler(timezone=os.environ.get("TZ", "UTC"))
    cron = os.environ.get("SYNC_SCHEDULE", "0 2 * * *")
    scheduler.add_job(runner.trigger, CronTrigger.from_crontab(cron),
                      args=["all"], id="main_sync")
    scheduler.start()
    if os.environ.get("SYNC_ON_STARTUP", "").lower() == "true":
        asyncio.create_task(runner.trigger("all"))
    yield
    scheduler.shutdown()
```

Surface `next_run_at` in `/api/sync/status`. **Already-running guard
comes for free** — `runner.trigger()` raises 409 if a sync is in flight.

Add `apscheduler>=3.10.0` to `requirements.txt`.

No UI for editing the schedule in v1. Cron expression in env only.

---

## Pod 6 — Documentation & Polish (revised)

Now informed by what shipped:

1. `README.md` — what + screenshots + 5-minute quick-start
2. `docs/SETUP.md` — Spotify app creation, Jellyfin/Lidarr API key extraction, OAuth troubleshooting
3. `docs/ARCHITECTURE.md` — the synthesis insights from this handoff (steal/avoid table, package layout, request flow)
4. `docs/TROUBLESHOOTING.md` — common OAuth failures, low match rate diagnosis, Lidarr state-machine stuck states
5. `docs/RECIPES.md` — docker-compose snippets for: Jellyfin on same host, remote Lidarr, exposing via Tailscale, behind nginx with auth
6. GitHub Actions workflow: build + push image to ghcr.io on tagged release
7. Makefile: `make build`, `make up`, `make logs`, `make sync` (one-shot)

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
