# Environment Variables

Every runtime knob is an env var. Required vars halt the app on startup if
missing; optional vars have sensible defaults.

## Required

| Variable | Purpose |
|---|---|
| `SPOTIFY_CLIENT_ID` | OAuth app from [developer.spotify.com](https://developer.spotify.com/dashboard) |
| `SPOTIFY_CLIENT_SECRET` | Same OAuth app |
| `JELLYFIN_API_KEY` | Jellyfin → Dashboard → API Keys |
| `JELLYFIN_USER_ID` | Jellyfin → Users → click your user → URL contains the GUID |
| `JELLYFIN_URL` | e.g. `http://jellyfin:8096` (reachable from the container) |
| `LIDARR_API_KEY` | Lidarr → Settings → General → API Key |
| `LIDARR_URL` | e.g. `http://lidarr:8686` |

## Optional — Spotify OAuth

| Variable | Default | Purpose |
|---|---|---|
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8888/callback` | Must match the URI registered on your Spotify app exactly. For headless or remote Docker hosts, set to `http://<host-ip>:8888/callback`. |

## Optional — runtime mode

| Variable | Default | Purpose |
|---|---|---|
| `SYNC_MODE` | `web` | `web` runs the FastAPI server; `oneshot` runs a single sync and exits (cron-friendly) |
| `SYNC_ON_STARTUP` | `false` | If `true`, kick off a sync as soon as the web server is ready |
| `SYNC_SCHEDULE` | `0 2 * * *` | Cron expression for automatic sync (uses `TZ` timezone). Set to empty string to disable the scheduler entirely. |
| `WEB_HOST` | `0.0.0.0` | Bind address |
| `WEB_PORT` | `8000` | Port the FastAPI server listens on |
| `API_KEY` | (empty) | If set, every `/api/*` request must include `X-API-Key: <value>`. Empty = no auth (suitable for trusted home networks only) |

## Optional — paths (don't change unless you know why)

| Variable | Default in container | Purpose |
|---|---|---|
| `SYNC_CONFIG` | `/app/config/config.json` | Main config file (playlists list, match thresholds) |
| `SYNC_STATE` | `/app/data/sync_state.json` | Lidarr request state machine persistence |
| `SPOTIFY_TOKEN_CACHE` | `/app/data/.spotify_token_cache` | Spotify refresh token (treat as a secret) |
| `LOG_FILE` | `/app/logs/spotify_sync.log` | Rotated by the host or a sidecar — the app does not rotate |
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
| `8000` | FastAPI | Map to host with `WEB_PORT` |
| `8888` | Spotify OAuth callback | Only needed during first-run authentication. Must be reachable from your **browser**, not just from the container. |

## First-run checklist

1. `cp .env.example .env` and fill in credentials
2. `docker compose up -d`
3. Browse to `http://<host>:8000/` — the dashboard guides you through Spotify OAuth (which uses port 8888)
4. Once `.spotify_token_cache` exists in `./data/`, port 8888 is no longer needed and can be closed at the firewall

## Security notes

- The container runs as a non-root `app` user (uid 1000)
- `.spotify_token_cache` is chmod 600 by the entrypoint
- The local API has no auth by default. If exposing beyond `127.0.0.1`, set `API_KEY` to a long random string
