# spotify_sync

Automatically sync your Spotify playlists into Jellyfin, with Lidarr handling
album acquisition for anything that's missing from your library.

```
Spotify playlist ──► match against Jellyfin library ──► add to Jellyfin playlist
                                                    └──► request missing albums in Lidarr
```

Built for self-hosters. Runs as a single Docker container alongside your
existing Jellyfin + Lidarr stack.

---

## Features

- **Web UI** — dashboard, playlist manager, live log tail, raw config editor
- **Per-playlist sync modes** — `add_only` (safe default), `full_sync` (mirror Spotify exactly), `rebuild` (wipe and recreate every run)
- **Bulk playlist management** — check multiple playlists, change sync mode or remove in one click
- **Scheduled sync** — cron expression via `SYNC_SCHEDULE` env var (default: 2 AM UTC daily)
- **Manual trigger** — sync all or a single playlist from the dashboard
- **Fuzzy matching** — RapidFuzz title + artist scoring with tuned thresholds; no false positives
- **MusicBrainz fallback** — when Lidarr doesn't know an artist yet, MusicBrainz resolves the MBID
- **Progress bar** — live current/total counter while a sync is running
- **SQLite history** — every sync run stored; survives container restarts cleanly
- **Optional API key** — empty = LAN-trust default; set for internet-exposed deployments

---

## Quick start

### 1 — Spotify developer app

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an app (any name/description)
3. Add `http://127.0.0.1:8888/callback` as a Redirect URI
4. Copy **Client ID** and **Client Secret**

See [docs/SETUP.md](docs/SETUP.md) for a full walkthrough.

### 2 — Clone and configure

```bash
git clone https://github.com/yourname/spotify_sync.git
cd spotify_sync
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
JELLYFIN_URL=http://jellyfin:8096          # reachable from inside the container
JELLYFIN_API_KEY=your_jellyfin_api_key
JELLYFIN_USER_ID=your_jellyfin_user_guid
LIDARR_URL=http://lidarr:8686
LIDARR_API_KEY=your_lidarr_api_key
```

> **Docker network note:** If Jellyfin and Lidarr run on a different Docker
> network than `spotify_sync`, use the gateway IP instead of service names.
> On a single-host homelab this is typically `172.18.0.1`. See
> [docs/RECIPES.md](docs/RECIPES.md).

### 3 — Fix volume permissions (Linux only, first run)

```bash
mkdir -p config data logs
sudo chown -R 1000:1000 config data logs   # container runs as uid 1000
```

### 4 — Start

```bash
make build
make up
```

Or without Make:

```bash
docker compose build
docker compose up -d
```

### 5 — Spotify OAuth (first run only)

1. Browse to `http://<host>:8000/`
2. On the **Dashboard**, click **Sync**
3. The container will open a local HTTP listener on port 8888
4. Your browser will be redirected there automatically — approve the Spotify permissions
5. The token is saved to `./data/.spotify_token_cache` and all future syncs are automatic

Port 8888 is only needed for this one-time step.

### 6 — Add playlists

Go to the **Playlists** view and paste a Spotify playlist URL or ID. Choose a
sync mode and an optional Jellyfin name, then click **Add**.

---

## Sync modes

| Mode | Behaviour |
|---|---|
| `add_only` | Tracks are only ever added to the Jellyfin playlist. Safe default — manual additions to the Jellyfin playlist are preserved. |
| `full_sync` | The Jellyfin playlist is updated to exactly mirror Spotify. Tracks removed from Spotify are removed from Jellyfin. |
| `rebuild` | The Jellyfin playlist is deleted and recreated from scratch on every run. Guarantees track order. |

---

## Make targets

```
make build      Build the Docker image
make rebuild    Force-rebuild (no layer cache)
make up         Start the container in the background
make down       Stop the container
make restart    Restart (picks up .env changes without rebuild)
make logs       Tail container logs
make status     Show container status + last sync result
make sync       Trigger a full sync via the API
make sync-one ID=<spotify_id>  Trigger sync for one playlist
make shell      Open a bash shell inside the container
make test       Run all pod smoke tests
make perms      Fix ./config ./data ./logs ownership (Linux first-run)
```

---

## Environment variables

See [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) for the full reference.

Key variables:

| Variable | Default | Notes |
|---|---|---|
| `SYNC_SCHEDULE` | `0 2 * * *` | Cron expression for automatic sync. Set to empty string to disable. |
| `SYNC_ON_STARTUP` | `false` | Trigger a sync immediately when the container starts. |
| `TZ` | `UTC` | Timezone for cron schedule and log timestamps. |
| `API_KEY` | (empty) | Set to a long random string to require `X-API-Key` on all API calls. |

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a full breakdown of the
package layout, request flow, and design decisions.

---

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues:
OAuth errors, low match rates, Lidarr stuck states, container permission
problems.

---

## Deployment recipes

See [docs/RECIPES.md](docs/RECIPES.md) for docker-compose snippets covering:
Jellyfin on the same host, remote Lidarr, Tailscale, nginx reverse proxy.

---

## License

MIT
