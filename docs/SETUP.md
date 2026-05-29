# Setup Guide

Step-by-step instructions for getting `octave` running from scratch.

---

## 1 — Spotify OAuth

Octave uses Spotify PKCE OAuth. A Client Secret is not required for normal
setup.

If your image ships a bundled Client ID, you can skip straight to **Connect
Spotify** in the Settings page. Otherwise:

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   and log in with your Spotify account.
2. Click **Create app**.
3. Fill in any name and description.
4. Set **Website** to anything, such as `http://localhost`.
5. Under **Redirect URIs**, add the callback URI shown by Octave Settings.
   The callback runs on the same port as the web UI (8000). For a local
   browser and Docker host, this is usually:
   ```
   http://127.0.0.1:8000/callback
   ```
   If your browser and Docker host are on different machines, use the host IP:
   ```
   http://192.168.1.50:8000/callback
   ```
6. Select **Web API** as the API to use.
7. Click **Save**, then copy the **Client ID** into Octave Settings.

---

## 2 — Jellyfin API key and User ID

### API key

1. In Jellyfin, go to **Dashboard → API Keys** (Administration section).
2. Click **+** to create a new key. Name it `octave`.
3. Copy the generated key — it is only shown once.

### User ID

1. In Jellyfin, go to **Dashboard → Users**.
2. Click your username.
3. Look at the browser URL — it ends in something like:
   ```
   /web/index.html#!/useredit.html?userId=abc123def456...
   ```
4. Copy that GUID.

Alternatively, call the API:
```bash
curl -s "http://jellyfin:8096/Users" \
  -H "X-Emby-Token: YOUR_API_KEY" \
  | python3 -m json.tool | grep -E '"Id"|"Name"'
```

---

## 3 — Lidarr API key

1. In Lidarr, go to **Settings → General**.
2. Scroll to **Security** — the **API Key** is shown there.
3. Copy it.

---

## 4 — Fill in .env

```bash
cp .env.example .env
$EDITOR .env
```

Minimum values:

```env
JELLYFIN_URL=http://jellyfin:8096
JELLYFIN_API_KEY=...
JELLYFIN_USER_ID=...

LIDARR_URL=http://lidarr:8686
LIDARR_API_KEY=...
```

> **Important:** `JELLYFIN_URL` and `LIDARR_URL` must be reachable from
> **inside the container**, not from your browser. If Jellyfin runs on a
> different Docker network, use the host IP or gateway IP.
> See [RECIPES.md](RECIPES.md) for examples.

---

## 5 — Volume permissions (Linux)

Docker creates bind-mount directories as root. The container runs as uid 1000.
Fix this once before the first `docker compose up`:

```bash
mkdir -p config data logs
sudo chown -R 1000:1000 config data logs
```

Or: `make perms`

---

## 6 — Build and start

```bash
make build
make up
```

Check the container is healthy:

```bash
make status
# or
docker compose ps
```

---

## 7 — Spotify OAuth

1. Browse to `http://<host>:8000/`
2. Open **Settings**, then click **Connect Spotify**
3. Spotify redirects your browser back to `http://<host>:8000/callback?code=...`,
   which the Octave web server handles directly (same port as the UI)
4. The code is exchanged for tokens and saved to `./data/.spotify_pkce_token`
5. All future syncs happen automatically using the stored refresh token

As long as `./data/.spotify_pkce_token`
exists and hasn't been revoked, you won't need to reauthenticate.

---

## 8 — Add playlists

Go to the **Playlists** view in the UI:

- Paste a Spotify playlist URL (e.g. `https://open.spotify.com/playlist/37i9...`)
  or just the ID (`37i9...`)
- Optionally set a custom Jellyfin playlist name
- Choose a sync mode (see [README.md](../README.md#sync-modes))
- Click **Add**

Alternatively, edit `./config/config.json` directly using the **Config** view
in the UI and trigger a sync.

---

## 9 — Verify the first sync

After adding playlists, click **Sync** on the Dashboard. Watch:

- The **status badge** — goes from `running` → `success` (or `error`)
- The **matched / missing / albums requested** counters
- The **Logs** view for per-track detail (`LOG_LEVEL=DEBUG` for full detail)

If tracks are missing from Jellyfin, Lidarr will have received album requests.
Check Lidarr's wanted list — once albums are downloaded and indexed, re-running
the sync will pick them up.
