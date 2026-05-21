# Octave -- Issues & Tracking

## Security Audit Findings (2026-05-19)

### Resolved
- **settings.json in .gitignore** -- UI-managed credentials file is now gitignored
- **settings.json in .dockerignore** -- excluded from Docker build context
- **Credentials configurable via UI** -- new Settings page with masked password fields, source badges (env/saved/unset)
- **Hot-reload of API_KEY** -- `os.environ` updated in-process when settings saved via UI

### Remaining Security Issues

| # | Severity | Issue | Notes |
|---|----------|-------|-------|
| 2 | Low | **API_KEY empty by default** | No auth on LAN. By design for LAN-trust model. Users must manually set API_KEY or use UI to generate one. |
| 3 | Low | **SSE auth gap** | `EventSource` cannot send custom headers. Live log stream disabled when API_KEY is set. Deferred fix. |
| 4 | Low | **Config writable when unauthenticated** | `PUT /api/config` unprotected if API_KEY is empty. Mitigated by LAN-trust assumption. |
| 5 | Low | **SQLite unencrypted** | Run history stored in plaintext SQLite. Low risk -- no credentials stored in DB. |
| 6 | Low | **No rate limiting** | API endpoints have no rate limiting. Low risk on LAN. Consider for internet-facing deployments. |
| 7 | Info | **Token cache is plaintext JSON** | `.spotify_token_cache` is chmod 600 but content is unencrypted JSON. Standard for OAuth libraries. |

## Known Issues

| # | Severity | Issue | Status |
|---|----------|-------|--------|
| 1 | Medium | **No "Test Connection" buttons in Settings UI** | Setup page already has reachability checks. Settings page could link to Setup for testing. |
| 2 | Low | **Nav overflow on very small screens** | 7 nav items may require horizontal scroll on <400px screens. Already handled by `overflow-x: auto`. |
| 3 | Low | **Settings changes to runtime knobs may require restart** | `SYNC_SCHEDULE` takes effect immediately. `LOG_LEVEL`, `TZ`, `WEB_PORT` require container restart. UI notes this. |
| 4 | Low | **No migration path from .env to settings.json** | Users can gradually migrate -- env vars take priority, so existing .env continues to work. |
| 5 | Info | **Em-dash encoding issues** | PowerShell `Set-Content` with heredocs corrupts UTF-8 em-dashes. Fixed by using `--` instead. |

## Changes Summary

### New Files
- `spotify_sync/web/settings.py` -- Persistent settings store (settings.json)
- `spotify_sync/web/routes/settings.py` -- GET/PUT /api/settings, POST /api/settings/rotate-api-key
- `spotify_sync/web/static/js/views/settings.js` -- Settings UI view

### Modified Files
- `spotify_sync/config.py` -- `_get_credential()` fallback to settings.json
- `spotify_sync/web/auth.py` -- `_get_api_key()` fallback to settings.json
- `spotify_sync/web/reachability.py` -- `_cred()` helper for settings.json fallback
- `spotify_sync/web/app.py` -- Registered settings route
- `spotify_sync/web/static/index.html` -- Added Settings nav link
- `spotify_sync/web/static/js/app.js` -- Registered settings view
- `spotify_sync/web/static/app.css` -- Settings view styles + mobile responsive
- `docker-compose.yml` -- All env vars now optional (empty defaults)
- `.gitignore` -- Added `settings.json`
- `.dockerignore` -- Added `settings.json`

## Architecture Decision

**Two-tier credential resolution**: `os.environ` > `settings.json` > default

This preserves backward compatibility -- existing `.env` files continue to work unchanged. Users can gradually migrate to UI-managed settings. The priority order ensures env vars always win, so power users can still override via `.env` for specific values.

**settings.json location**: `/app/data/settings.json` (inside the existing data volume mount)

This means settings persist across container restarts and are included in any backup of the data volume. The file is gitignored and dockerignore'd.

**No container restart required for most changes**: The `save_settings()` function updates `os.environ` in-process, so API_KEY and credential changes take effect immediately for new requests. The sync scheduler reads `SYNC_SCHEDULE` on every job execution, so schedule changes also take effect immediately.

## Fix: 2026-05-19   Settings Save Lockout + Test All

### Bugs resolved
1. **Save locks user out (401 after first API_KEY save)**   `save_settings()` updates `os.environ["API_KEY"]` in-process, so the subsequent `load()` call in the frontend's `save()` path got a 401. Fix: `setApiKey(updates.API_KEY)` is called **before** the PUT request in `save()`.
2. **localStorage never populated on first save**   guard `if (stored && stored !== updates.API_KEY)` returned false when `stored` was null. Fix: removed guard; `setApiKey()` is called unconditionally when `API_KEY` is in the save payload.
3. **No feedback after save**   `load()` would fail silently after lockout, leaving stale form. Fix: API key pre-store allows `load()` to succeed, confirming save.

### Feature added
- **"Test All" button**   calls `saveAndTest()` which saves settings then pings all integrations (Spotify, Jellyfin, Lidarr, ListenBrainz, Last.fm) via `/api/setup/status`, reporting pass/fail in a single toast.

### Known remaining
- **Spotify OAuth token**   no user token cache exists; client-credentials fallback can't access user playlists. Need OAuth flow from UI (separate task).
- **Example playlist 404**   `37i9dQZF1DXcBWIGoYBM5M` ("Today's Top Hits") returned 404 from Spotify API; may need user auth or the playlist ID changed upstream.
