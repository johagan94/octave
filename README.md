<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo.svg">
    <img alt="Octave" src="docs/logo.svg" width="120">
  </picture>
</p>

<h1 align="center">Octave</h1>

<p align="center">
  Spotify → Jellyfin + Lidarr sync. With ListenBrainz &amp; Last.fm enrichment.
</p>

<p align="center">
  <a href="https://github.com/jackohagan94-afk/octave/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/jackohagan94-afk/octave?style=flat-square"></a>
</p>

---

```
Spotify playlist ──► match against Jellyfin library ──► add to Jellyfin playlist
                         │                               └──► set cover art
                         └──► request missing albums in Lidarr
                              └──► ListenBrainz MBID resolution
                                   └──► Last.fm similar-artist discovery
```

Octave keeps your Spotify playlists perfectly mirrored in Jellyfin. When tracks
are missing from your library, it requests the albums in Lidarr — then tracks
them across runs until they appear. One container, no manual work.

---

## Features

- **Web UI** — onyx dark dashboard, playlist manager, live log tail, config editor
- **Per-playlist sync modes** — `add_only` (safe), `full_sync` (mirror), `rebuild` (wipe/recreate)
- **Bulk playlist management** — select multiple, change mode, or remove in one click
- **Scheduled sync** — cron via `SYNC_SCHEDULE` (default: 2 AM UTC)
- **Manual trigger** — sync all or a single playlist from the dashboard
- **Fuzzy matching** — RapidFuzz title + artist scoring with tuned thresholds
- **Track cache** — persistent spotify_id → jellyfin_id mapping; >10× faster on warm runs
- **Persistent library index** — Jellyfin library cached to disk for instant warm starts
- **Parallel Lidarr requests** — up to 4 concurrent album lookups
- **Cover art** — automatically pulled from Spotify and uploaded to Jellyfin playlists
- **Missing tracks view** — browse unmatched tracks per playlist, download CSV
- **Duplicate detection** — warns on in-playlist dupes; prevents cross-run Lidarr spam
- **waiting_for_lidarr** — tracks queued albums across runs so you know what's in flight
- **Compilation guard** — avoids matching compilation albums to wrong artists
- **SQLite history** — every run stored; survives restarts cleanly
- **Optional API key** — empty = LAN-trust; set for internet deployments
- **Client-credentials fallback** — works without Spotify OAuth for public playlists
- **Optional ListenBrainz** — MBID resolution, global popularity data
- **Optional Last.fm** — playcounts, similar track/artist discovery
- **Fully responsive** — works on phone, tablet, and desktop

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
git clone https://github.com/jackohagan94-afk/octave.git
cd octave
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
JELLYFIN_URL=http://jellyfin:8096
JELLYFIN_API_KEY=your_jellyfin_api_key
JELLYFIN_USER_ID=your_jellyfin_user_guid
LIDARR_URL=http://lidarr:8686
LIDARR_API_KEY=your_lidarr_api_key
```

### 3 — Fix volume permissions (Linux only, first run)

```bash
mkdir -p config data logs
sudo chown -R 1000:1000 config data logs
```

### 4 — Start

```bash
docker compose build
docker compose up -d
```

### 5 — Add playlists

Browse to `http://localhost:8000` → **Playlists** → paste a Spotify URL or ID.

---

## Sync modes

| Mode | Behaviour |
|---|---|
| `add_only` | Tracks are only ever added. Manual Jellyfin edits preserved. |
| `full_sync` | Mirrors Spotify exactly — removals from Spotify are reflected. |
| `rebuild` | Deletes and recreates the Jellyfin playlist from scratch every run. |

---

## Optional integrations

| Service | Env var | What it does |
|---|---|---|
| **ListenBrainz** | `LISTENBRAINZ_TOKEN` | MusicBrainz ID resolution, global popularity stats |
| **Last.fm** | `LASTFM_API_KEY` | Playcounts, similar tracks/artists, scrobble metadata |

---

## Environment variables

See [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) for the full reference.

| Variable | Default | Notes |
|---|---|---|
| `SYNC_SCHEDULE` | `0 2 * * *` | Cron for auto-sync. Empty to disable. |
| `SYNC_ON_STARTUP` | `false` | Trigger sync immediately on boot. |
| `TZ` | `UTC` | Timezone for cron and log timestamps. |
| `API_KEY` | (empty) | Require `X-API-Key` on all API calls. |
| `LISTENBRAINZ_TOKEN` | (empty) | Optional — enables MBID/popularity features. |
| `LASTFM_API_KEY` | (empty) | Optional — enables playcounts/discovery. |

---

## Docs

- [Setup guide](docs/SETUP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Deployment recipes](docs/RECIPES.md)

---

## License

MIT
