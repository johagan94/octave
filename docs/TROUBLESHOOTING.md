# Troubleshooting

Solutions to the most common problems.

---

## Spotify OAuth

### "INVALID_CLIENT: Invalid redirect URI" from Spotify

**Symptom:** Spotify shows an error instead of the authorize prompt, or the
callback fails with a redirect-URI mismatch.

**Cause:** The redirect URI registered in your Spotify app does not exactly
match the one Octave sends.

**Fix:**

1. In Octave **Settings → Connect Spotify**, note the redirect URI shown.
2. In developer.spotify.com/dashboard → your app → **Edit Settings → Redirect URIs**,
   add that exact URL, e.g. `http://<host-ip>:8000/callback` (the callback runs
   on the web-UI port, 8000).
3. If you front Octave with a reverse proxy, set `SPOTIFY_REDIRECT_URI` to the
   public URL (e.g. `https://octave.example.com/callback`) and register that same
   URL with Spotify. The URLs must match character-for-character.

### Callback shows NXDOMAIN / "can't reach this page" after authorizing

**Symptom:** Spotify accepts the login, then the browser fails to load the
`/callback` URL with NXDOMAIN or a connection error — even though the redirect
URI is registered in your Spotify app.

**Cause:** This is a DNS/reachability problem, not an Octave bug. The redirect
URI (e.g. `https://octave.example.com/callback`) does not resolve from the
machine running the browser — common when the hostname only exists in LAN DNS
and you're authorizing from a device using a different resolver.

**Fix:**

1. Confirm the hostname resolves from the browser's machine
   (`nslookup octave.example.com`). If it doesn't, add a public DNS record / a
   Cloudflare Tunnel, or a LAN DNS rewrite / hosts entry pointing at your
   reverse proxy.
2. Or authorize entirely on the host: set `SPOTIFY_REDIRECT_URI` to
   `http://127.0.0.1:8000/callback`, register that in Spotify, and open the
   Octave UI from the server's own browser. (Spotify only allows plain `http`
   for the `127.0.0.1` loopback — a LAN IP like `http://192.168.x.x:8000` is
   rejected.)

> **Note:** `SPOTIFY_REDIRECT_URI` is read from the **environment first**, then
> `settings.json`. If you set it as a container/`.env` variable, editing it in
> the Settings UI has no effect until you change the env var (the field shows an
> orange **env** badge in that case). This precedence applies to every setting.

### "No Spotify Client ID available" (HTTP 400) on Connect

**Cause:** No Client ID is configured. The public image does not ship a bundled
Spotify app.

**Fix:** Create a free app at developer.spotify.com/dashboard and paste its
Client ID into **Settings → Spotify** (no client secret is needed — Octave uses
PKCE).

### Connected once, now syncs fail with an auth error

**Cause:** The stored token was revoked (e.g. you removed the app from your
Spotify account) or the Client ID changed.

**Fix:** Click **Disconnect** then **Connect Spotify** again to re-authorize. The
token lives at `./data/.spotify_pkce_token`.

---

### Sync is permanently stuck at "running" after a container restart

**Symptom:** The Dashboard shows "Syncing…" forever and the Sync button is
disabled. This happens when the container was restarted mid-sync.

**Fix:** This is already handled in `runner.py` — on startup, any run in the
DB with `status="running"` is immediately marked `error` with the message
"interrupted by container restart". Restart the container:

```bash
make restart
```

If the problem persists after upgrading, it means the old version wrote a
stuck "running" row before the fix was in place. Fix it manually:

```bash
docker compose exec octave sqlite3 /app/data/octave.db \
  "UPDATE sync_runs SET status='error', finished_at=datetime('now'), \
   error='interrupted' WHERE status='running';"
make restart
```

---

### Spotify 403 errors

**Symptom:** Sync fails with HTTP 403 while reading Spotify playlists.

**Cause:** The stored OAuth token may be stale, missing playlist scopes, or
associated with a different Spotify account than the playlists you are trying
to read.

**Fix:**

1. Open Settings and confirm Spotify is connected.
2. Confirm the playlist is owned by, followed by, or visible to the connected
   account.
3. Refresh the token by deleting the cache and re-authenticating:
   ```bash
   rm ./data/.spotify_pkce_token
   make restart
   ```
   Then trigger a sync to redo the OAuth flow.

---

### "No token cache" — OAuth was never completed

**Symptom:** `/api/setup/status` shows `spotify: {configured: true, reachable: false}`
with an error like `no token cache`.

**Fix:** Complete the OAuth flow:
1. Browse to `http://<host>:8000/`
2. Click **Sync** on the Dashboard
3. Approve the Spotify permissions in your browser
4. Verify `./data/.spotify_pkce_token` now exists

---

## Jellyfin

### Playlists not appearing in Jellyfin

**Symptom:** Sync runs succeed but playlists don't show up in the Jellyfin UI.

**Check:**

1. Correct `JELLYFIN_USER_ID`? The playlist is created under that user's
   account. Log in as that user in Jellyfin to see it.
2. Jellyfin sometimes needs a library scan to pick up newly added items.
   Trigger one: Jellyfin → Dashboard → Libraries → Scan All.

---

### Duplicate playlists created every sync (e.g. several copies of one name)

**Symptom:** A playlist is recreated on every sync, leaving many Jellyfin
playlists with the same name.

**Cause:** The configured playlist name had leading/trailing whitespace (e.g.
`"Thank You Based God "`). Jellyfin trims the name when it stores the playlist,
so Octave's name lookup missed the trimmed copy and created a new one each run.
Fixed in 3.3.3 — names are normalized (trimmed + whitespace-collapsed) for both
matching and creation, and the resolved playlist id is persisted so subsequent
runs reuse it.

**Clean up existing duplicates:**

1. Update to 3.3.3+.
2. In Jellyfin, delete the extra duplicate playlists, keeping one.
3. Remove any stray spaces from `jellyfin_playlist_name` in `config.json`.
The next sync will reuse the remaining playlist instead of making new ones.

---

### `JELLYFIN_URL` connection refused

**Symptom:** Setup page shows `jellyfin: {reachable: false}` with
`Connection refused` or `Name or service not known`.

**Fix:** The URL must be reachable from **inside the container**, not from your
browser. Common mistakes:

| Wrong | Right |
|---|---|
| `http://localhost:8096` | `http://172.18.0.1:8096` (host gateway IP) |
| `http://127.0.0.1:8096` | `http://jellyfin:8096` (same Docker network) |
| `http://192.168.1.5:8096` | May work if routed, but test first |

To find your gateway IP:
```bash
docker network inspect octave_default | grep Gateway
```

See [RECIPES.md](RECIPES.md) for worked examples.

---

## Lidarr

### Albums stuck in "pending" state

**Symptom:** Tracks remain in `missing_tracks.json` / missing counter doesn't
go down across multiple syncs.

**Check:**

1. Is the Lidarr album download actually completing? Check Lidarr's Activity
   and Wanted pages.
2. Is the metadata/library scan finishing in Lidarr? Lidarr must index the
   files before the next sync can find them.
3. Is the artist name significantly different between Spotify and Lidarr? The
   fuzzy matcher uses a ≥85 threshold for album lookups. Enable `LOG_LEVEL=DEBUG`
   to see scores:
   ```bash
   LOG_LEVEL=DEBUG make restart
   make logs | grep -i match
   ```

---

### Lidarr shows duplicate album requests

**Symptom:** The same album appears multiple times in Lidarr's wanted list.

**Cause:** The state machine in `sync_state.json` tracks which albums have
been requested. If `sync_state.json` is lost or reset, the next sync will
re-request everything.

**Fix:** The state file lives at `./data/sync_state.json`. Don't delete it
unless you want to reset all request tracking.

---

## Container / Docker

### Volume permission denied

**Symptom:** Container logs contain `Permission denied` when writing to
`/app/config/`, `/app/data/`, or `/app/logs/`.

**Fix:** Docker creates bind-mount directories as root. The container runs as
uid 1000. Run once before starting the container:

```bash
sudo chown -R 1000:1000 ./config ./data ./logs
# or:
make perms
```

---

### `config.json` is the example file (empty playlists list)

**Symptom:** UI shows 0 playlists, but you have a `config.json` at the repo root.

**Cause:** On first run, the entrypoint seeds `./config/config.json` from
`config.example.json` if the file doesn't exist. Your real config is at the
repo root, not inside the `./config/` volume directory.

**Fix:**

```bash
cp config.json config/config.json
make restart
```

Or use the **Config** view in the UI to paste your playlist JSON directly.

---

## Matching / low match rate

### Most tracks show as "missing" even though they're in Jellyfin

**Likely causes:**

1. **Track titles contain edition/version info** — e.g. "Song Name (Remastered 2011)"
   vs "Song Name". The `normalise()` function strips many of these, but edge
   cases exist. Enable `LOG_LEVEL=DEBUG` to see what's being compared.

2. **Artist name mismatch** — "The Beatles" vs "Beatles". The fuzzy match
   handles common prefixes, but extreme mismatches fail.

3. **Wrong Jellyfin library** — the matcher searches the entire library for the
   user. If your music library isn't fully indexed in Jellyfin, items won't appear
   in search results.

4. **Thresholds too strict for your library** — the defaults (title≥75, artist≥65,
   combined≥80) are tuned conservatively. If you're confident your library naming
   is consistent, you can lower them in `config.json` under `match_thresholds`.

**Diagnostic steps:**

```bash
# Enable debug logging and restart
LOG_LEVEL=DEBUG make restart

# Tail logs while triggering a sync
make logs &
make sync

# Filter for match-related lines
docker compose logs | grep -E 'match|score|threshold'
```

---

## Scheduler

### Cron fires at the wrong time

**Check `TZ`** — the cron schedule uses the `TZ` env var timezone. If `TZ=UTC`
and you set `SYNC_SCHEDULE=0 2 * * *`, the sync fires at 02:00 UTC, not 02:00
in your local timezone.

```env
TZ=Australia/Sydney
SYNC_SCHEDULE=0 2 * * *   # now fires at 02:00 AEST/AEDT
```

Verify the next run time in the Dashboard or via:

```bash
curl -s http://localhost:8000/api/sync/status | python3 -m json.tool | grep next_run
```

### Scheduler is disabled unexpectedly

If `SYNC_SCHEDULE` is set to an empty string in `.env`, the scheduler is
intentionally disabled. Check your `.env`:

```bash
grep SYNC_SCHEDULE .env
```

An invalid cron expression also disables the scheduler (it logs an error at
startup). Check `make logs` for `[web] invalid SYNC_SCHEDULE`.
