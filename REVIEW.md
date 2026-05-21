# Octave — Code & Security Review
**Date:** 2026-05-22
**Branch:** dev-main (octave-dev on gitea)
**Latest commit:** fix: atomic state writes, redirect URI override, add tests and ISSUES.md

---

## Scope Analyzed

Full codebase review covering:
- **Core sync pipeline:** `octave/__main__.py`, `octave/sync.py`, `octave/state.py`
- **Spotify integration:** `octave/spotify_auth.py`, `octave/spotify_client.py`
- **Jellyfin/Lidarr clients:** `octave/jellyfin_client.py`, `octave/lidarr_client.py`
- **Web layer:** `octave/web/app.py`, `octave/web/auth.py`, `octave/web/runner.py`, `octave/web/settings.py`, `octave/web/db.py`
- **API routes:** All routes under `octave/web/routes/`
- **Infrastructure:** `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `.github/workflows/docker.yml`
- **Tests:** `tests/test_state.py`

---

## Key Findings

### SECURITY

#### S-1: Medium — CSV injection in missing tracks download
**File:** `octave/web/routes/sync.py:113-118`

The CSV export (`/api/sync/missing/download/{spotify_id}`) writes track title, artist, and album directly into CSV without quoting or escaping. If a track name contains `=`, `+`, `-`, or `@` at the start of a cell, it can execute formula injection when opened in Excel/Google Sheets.

**Fix:** Use Python's `csv` module with `csv.writer` instead of manual string concatenation, or prefix cells starting with dangerous characters with a single quote.

#### S-2: Medium — Token file written without fsync
**File:** `octave/spotify_auth.py:91-107`

`_save_token()` writes to `.tmp` then calls `tmp.replace(path)` but does **not** `fsync()` before the rename. A crash between write and rename can corrupt the token file (contains the long-lived refresh token). Compare with `octave/state.py:31-39` which correctly uses `fh.flush()` + `os.fsync()` before `os.replace()`.

**Fix:** Add `f.flush(); os.fsync(f.fileno())` before `tmp.replace(path)` in `_save_token()`.

#### S-3: Low — Settings.json has no file permissions enforcement
**File:** `octave/web/settings.py:108-138`

`settings.json` stores API keys, tokens, and passwords in plaintext. Unlike the Spotify token cache (which is `chmod 0600`), `settings.json` has no permission hardening. On a shared filesystem or compromised container, any process can read all credentials.

**Fix:** Add `os.chmod(path, 0o600)` after writing `settings.json` (same pattern as `_save_token`).

#### S-4: Low — `run_sync()` uses `datetime.utcnow()` (deprecated)
**File:** `octave/__main__.py:41`

`datetime.datetime.utcnow()` is deprecated in Python 3.12+ and produces naive timestamps. Should use `datetime.now(timezone.utc)` for consistency with the rest of the codebase (e.g., `runner.py:31`).

---

### CODE QUALITY

#### C-1: Medium — `_state_lock` acquired twice in `request_album_in_lidarr`
**File:** `octave/sync.py:59-72`

The function acquires `_state_lock` at line 66, then re-reads state inside the lock — but the same lock was already acquired at line 59 in the outer scope of the caller's `sync_playlist`. Since `_state_lock` is a `threading.Lock` (not `RLock`), this will **deadlock** if called from a context where the lock is already held. Currently `sync_playlist` does not hold the lock when calling `request_album_in_lidarr`, but this is fragile — any future refactoring that wraps the call in `_state_lock` will deadlock.

**Fix:** Either document the precondition clearly, or use `threading.RLock` for reentrancy safety.

#### C-2: Medium — Duplicate lock acquisition pattern in `request_album_in_lidarr`
**File:** `octave/sync.py:59-72`

The same state is read twice — once outside the lock (line 59-61) and once inside (line 66-69). The first read is useless since the result is discarded. This is dead code that adds confusion.

**Fix:** Remove the first read (lines 59-64). Only read inside the lock.

#### C-3: Low — `spotify_client.py` has unused OAuth callback handler
**File:** `octave/spotify_client.py:53-83`

`_OAuthCallbackHandler` and `_run_local_server` are legacy code for the old Authorization Code flow. The PKCE flow in `spotify_auth.py` has its own `_CallbackHandler`. This duplicate handler is only used when `client_secret` is set AND no PKCE token exists — a path that is increasingly rare.

**Fix:** Consider consolidating or deprecating the legacy handler. At minimum, add a comment explaining when it's used.

#### C-4: Low — `settings.py` `_read_raw()` not protected by lock
**File:** `octave/web/settings.py:55-64`

`_read_raw()` reads `settings.json` without acquiring `_lock`, while `save_settings()` writes under the lock. Concurrent reads during a write (via `tmp.replace()`) could theoretically read a partially-written file on some filesystems. The `tmp.replace()` pattern is atomic on POSIX, but the lock should still guard reads for consistency.

**Fix:** Have `_read_raw()` acquire `_lock` for the read, or document that `tmp.replace()` atomicity makes this safe.

#### C-5: Low — `db.py` creates a new connection per operation
**File:** `octave/web/db.py:63-70`

`_connect()` opens a new `sqlite3.connect()` on every call, then closes it in `cursor()`. This is inefficient under load. SQLite connection pooling is not needed, but a single shared connection with WAL mode would be more efficient.

**Fix:** Consider a module-level singleton connection, or at minimum document the rationale for per-call connections.

#### C-6: Low — CI workflow references missing test scripts
**File:** `.github/workflows/docker.yml:77-83`

The `smoke-test` job references `_pod1_check.py`, `_pod3_check.py`, and `_pod4_rebuild_check.py` which do not exist in the repository. This will cause CI to fail on PRs to `main`.

**Fix:** Either create these test scripts or remove the smoke-test job until they exist.

---

### WHAT WAS DONE WELL

1. **PKCE OAuth** — Correct implementation with state/verifier lifecycle, pending session pruning, and atomic token storage with file permissions.
2. **Atomic state writes** — `state.py` correctly uses write-to-tmp + fsync + os.replace pattern.
3. **HTTP Basic Auth** — Uses `secrets.compare_digest()` for timing-safe comparison. Health endpoint correctly exempted.
4. **Thread safety** — Per-artist locks in Lidarr client, `_state_lock` for state mutations, asyncio.Lock in runner.
5. **Graceful degradation** — Missing APScheduler, Pillow, or optional integrations (ListenBrainz/Last.fm) are handled cleanly.
6. **Response envelope** — Consistent `ApiResponse[T]` pattern with Pydantic models across all routes.
7. **Docker security** — Non-root user, tini init, multi-stage build, healthcheck.
8. **Credential resolution** — Three-tier fallback (env > settings.json > default) is well-designed and backward compatible.

---

## Recommended Fixes (Priority Order)

| Priority | ID | Action | Effort |
|----------|-----|--------|--------|
| High | S-1 | Use `csv.writer` for CSV export to prevent formula injection | 10 min |
| High | S-2 | Add `fsync()` to `_save_token()` in `spotify_auth.py` | 5 min |
| Medium | S-3 | `chmod 0600` on `settings.json` after write | 5 min |
| Medium | C-1/C-2 | Remove duplicate state read, consider RLock | 10 min |
| Low | C-6 | Fix CI workflow (remove or create missing test scripts) | 30 min |
| Low | S-4 | Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` | 5 min |

---

## Residual Risk

- **No rate limiting** on API endpoints — acceptable for LAN deployments but should be documented for internet-facing use.
- **SQLite WAL + single writer** — fine for single-process, but would need serialization if scaled horizontally.
- **Spotify token cache** is plaintext JSON — standard for OAuth libraries, but worth noting for compliance contexts.
- **Legacy OAuth path** in `spotify_client.py` is untested and may have edge-case failures with modern Spotify API changes.
