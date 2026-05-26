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
  <a href="https://github.com/johagan94/octave/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/johagan94/octave?style=flat-square"></a>
</p>

---

```
Spotify playlist ──► match against Jellyfin library ──► add to Jellyfin playlist
                         │                               └──► set cover art
                         └──► request missing albums in Lidarr
                              └──► ListenBrainz MBID resolution
                                   └──► Last.fm similar-artist discovery
```

Octave keeps your Spotify playlists in Jellyfin. When tracks are missing from
your library, it can request albums in Lidarr and track them across runs until
they appear. One container, local-first, with a web UI for setup.

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
- **HTTP Basic Auth** — optional browser-native auth for exposed deployments
- **PKCE Spotify OAuth** — no client secret required for normal setup
- **Sync all playlists** — discover owned/followed Spotify playlists automatically
- **Optional ListenBrainz** — MBID resolution, global popularity data
- **Optional Last.fm** — playcounts, similar track/artist discovery
- **Fully responsive** — works on phone, tablet, and desktop

---

## Quick start

### 1 — Clone and start

```bash
git clone https://github.com/johagan94/octave.git
cd octave
cp .env.example .env
mkdir -p config data logs
# Linux only: make bind mounts writable by the container user.
sudo chown -R 1000:1000 config data logs
docker compose up -d --build
```

### 2 — Configure in the web UI

Open `http://localhost:8000`, then fill in Settings:

- Spotify: create a Spotify app, paste its Client ID in Settings, then click
  **Connect Spotify**. PKCE does not need a Client Secret, though Octave still
  accepts one for legacy setups.
- Jellyfin: URL, API key, and user ID.
- Lidarr: URL and API key if you want missing albums requested automatically.

See [docs/SETUP.md](docs/SETUP.md) for a full walkthrough.

### 3 — Linux volume permissions

If you skipped the permission step before first start, run it and restart:

```bash
mkdir -p config data logs
sudo chown -R 1000:1000 config data logs
docker compose restart
```

### 4 — Add playlists

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
| **Spotify** | `SPOTIFY_CLIENT_ID` | Your Spotify app Client ID for PKCE auth |
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
| `AUTH_USERNAME` | `octave` | HTTP Basic Auth username. |
| `AUTH_PASSWORD` | (empty) | Empty disables auth; set a password before exposing Octave. |
| `LISTENBRAINZ_TOKEN` | (empty) | Optional — enables MBID/popularity features. |
| `LASTFM_API_KEY` | (empty) | Optional — enables playcounts/discovery. |
| `LASTFM_USERNAME` | (empty) | Optional — used for Last.fm scrobble import workflows. |

---

## Docs

- [Setup guide](docs/SETUP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Deployment recipes](docs/RECIPES.md)

---

## License

MIT
