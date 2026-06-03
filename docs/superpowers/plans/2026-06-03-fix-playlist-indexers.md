# Playlist Indexer Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Octave from creating duplicate Jellyfin playlists for the same Spotify playlist, including repeated custom-name syncs such as "thank you based god".

**Architecture:** Store a durable Spotify playlist ID to Jellyfin playlist ID mapping in sync state, then resolve playlist identity from that mapping before falling back to case-insensitive name lookup or creation. In rebuild mode, delete all same-name Jellyfin playlists so existing duplicates are collapsed into one fresh playlist.

**Tech Stack:** Python, FastAPI-side shared models/state, pytest, Jellyfin REST client abstractions.

---

### Task 1: Capture The Duplicate Creation Regression

**Files:**
- Modify: `tests/test_sync_failures.py`

- [x] **Step 1: Write failing tests**

Add tests that sync a playlist with a stored `state["jellyfin_playlists"][spotify_id]` mapping and assert the existing Jellyfin playlist ID is reused without calling `get_or_create_playlist`, then add a rebuild-mode test with two same-name Jellyfin playlists and assert both are deleted before the replacement is created.

- [x] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_sync_failures.py -q`
Expected: FAIL because `sync_playlist` currently ignores `state["jellyfin_playlists"]` and rebuild mode breaks after deleting the first same-name playlist.

### Task 2: Add State-Backed Playlist Resolution

**Files:**
- Modify: `octave/sync.py`

- [x] **Step 1: Implement helpers**

Add small helpers in `octave/sync.py` to ensure the state mapping exists, verify a stored Jellyfin playlist ID is still present in `jf.get_playlists()`, update the mapping after fallback lookup/create, and remove stale mappings when the referenced playlist no longer exists.

- [x] **Step 2: Wire add/full sync modes through the helper**

Replace direct `jf.get_or_create_playlist(jf_name)` in non-rebuild modes with the state-backed resolver, then persist the updated mapping with `save_state(state)`.

- [x] **Step 3: Wire rebuild mode through duplicate cleanup**

In rebuild mode, delete every playlist whose name case-insensitively matches `jf_name`, clear any stale mapped ID, create a fresh playlist, and store that fresh ID in `state["jellyfin_playlists"][spotify_id]`.

### Task 3: Verify And Publish

**Files:**
- Modify: `docs/superpowers/plans/2026-06-03-fix-playlist-indexers.md`

- [x] **Step 1: Run focused tests**

Run: `python -m pytest tests/test_sync_failures.py -q`
Expected: PASS.

- [x] **Step 2: Run full tests**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit and push**

Stage only `octave/sync.py`, `octave/jellyfin_client.py`, `tests/test_sync_failures.py`, and this plan file. Commit as `fix(sync): persist Jellyfin playlist index`, push `codex/fix-playlist-indexers` to GitHub, open a PR against `main`, and auto-merge only if GitHub reports the PR mergeable with no conflicts.
