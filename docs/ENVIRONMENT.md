# Environment Variables

Every runtime knob can be supplied as an env var. Most credentials can also be
saved from the web UI Settings page; env vars take priority over saved settings.

## Required for sync

| Variable | Purpose |
|---|---|
| `SPOTIFY_CLIENT_ID` | Your Spotify app Client ID for PKCE auth |
| `JELLYFIN_API_KEY` | Jellyfin → Dashboard → API Keys |
| `JELLYFIN_USER_ID` | Jellyfin → Users → click your user → URL contains the GUID |
| `JELLYFIN_URL` | e.g. `http://jellyfin:8096` (reachable from the container) |
| `LIDARR_API_KEY` | Optional. Lidarr → Settings → General → API Key |
| `LIDARR_URL` | Optional. e.g. `http://lidarr:8686` |

## Optional — Spotify OAuth

| Variable | Default | Purpose |
|---|---|---|
| `OCTAVE_BUNDLED_SPOTIFY_CLIENT_ID` | (empty) | Optional maintainer-supplied public Client ID for private builds. Most public users should use `SPOTIFY_CLIENT_ID`. |
| `SPOTIFY_CLIENT_SECRET` | (empty) | Legacy OAuth support only; normal PKCE setup does not need it. |
| `SPOTIFY_REDIRECT_URI` | auto | Explicit callback URI. Leave blank to let the UI infer a LAN callback URI. |

## Optional — runtime mode

| Variable | Default | Purpose |
|---|---|---|
| `SYNC_MODE` | `web` | `web` runs the FastAPI server; `oneshot` runs a single sync and exits (cron-friendly) |
| `SYNC_ON_STARTUP` | `false` | If `true`, kick off a sync as soon as the web server is ready |
| `SYNC_SCHEDULE` | `0 2 * * *` | Cron expression for automatic sync (uses `TZ` timezone). Set to empty string to disable the scheduler entirely. |
| `WEB_HOST` | `0.0.0.0` | Bind address |
| `WEB_PORT` | `8000` | Port the FastAPI server listens on |
| `AUTH_USERNAME` | `octave` | HTTP Basic Auth username. |
| `AUTH_PASSWORD` | (empty) | Empty disables auth. Set a password before exposing Octave outside a trusted LAN/VPN. |
| `LASTFM_USERNAME` | (empty) | Optional Last.fm username for scrobble import workflows. |

## Optional — paths (don't change unless you know why)

| Variable | Default in container | Purpose |
|---|---|---|
| `SYNC_CONFIG` | `/app/config/config.json` | Main config file (playlists list, match thresholds) |
| `SYNC_STATE` | `/app/data/sync_state.json` | Lidarr request state machine persistence |
| `SPOTIFY_TOKEN_CACHE` | `/app/data/.spotify_pkce_token` | Spotify refresh token (treat as a secret) |
| `LOG_FILE` | `/app/logs/octave.log` | Rotated by the host or a sidecar — the app does not rotate |
| `LOG_LEVEL` | `INFO` | `DEBUG` for matching diagnostics |
| `TZ` | `UTC` | Container timezone — affects timestamps in logs and the cron scheduler. Use an IANA name, e.g. `Australia/Sydney`. |

## Volume layout

| Host path | Container path | Contents |
|---|---|---|
| `./config` | `/app/config` | `config.json` (user-editable) |
| `./data`   | `/app/data`   | State + token cache + run-history database |
| `./logs`   | `/app/logs`   | Application log |

## Port layout

| Port | Used by | Notes |
|---|---|---|
| `8000` | FastAPI web UI **and** the Spotify OAuth `/callback` | Map to host with `WEB_PORT`. Must be reachable from your **browser**. |
| `8888` | Advanced/legacy only | Fallback OAuth listener, started only when a custom `SPOTIFY_REDIRECT_URI` targets a non-8000 port. Not mapped by default. |

## First-run checklist

1. `cp .env.example .env`
2. `docker compose up -d`
3. Browse to `http://<host>:8000/` and configure Settings
4. Click **Connect Spotify**; the OAuth callback is handled on port 8000 (same as the web UI)
5. Once `.spotify_pkce_token` exists in `./data/`, authentication persists across restarts

## Security notes

- The container runs as a non-root `app` user (uid 1000)
- `.spotify_pkce_token` is chmod 600 by the auth writer/entrypoint where supported
- The API has no auth by default. If exposing beyond a trusted LAN/VPN, set `AUTH_PASSWORD`
