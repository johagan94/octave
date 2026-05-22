# Troubleshooting

Solutions to the most common problems.

---

## Spotify OAuth

### `ERR_CONNECTION_RESET` when Spotify redirects to port 8888

**Symptom:** Browser shows "This site can't be reached — ERR_CONNECTION_RESET"
on `http://127.0.0.1:8888/callback`.

**Cause:** The OAuth callback server inside the container was binding to
`127.0.0.1` (container-internal loopback). Docker port mapping only works with
`0.0.0.0`.

**Fix:** This is already patched in `spotify_client.py`. If you're on an older
version, update to the current code and rebuild.

**If you still get this error:**

1. Confirm port 8888 is exposed: `docker compose ps` should show `0.0.0.0:8888->8888/tcp`
2. Check your firewall allows port 8888 from your browser machine
3. If your browser and Docker host are on different machines, set:
   ```env
   SPOTIFY_REDIRECT_URI=http://<host-ip>:8888/callback
   ```
   And add the same URL to your Spotify app's Redirect URIs list.

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
