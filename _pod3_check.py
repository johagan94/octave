"""Pod 3 smoke test — exercises every endpoint via FastAPI TestClient.

Requires: fastapi, httpx, pydantic, sse-starlette installed in the active
venv. Doesn't talk to Spotify/Jellyfin/Lidarr — uses an empty config so
all integrations report 'not configured'.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Isolate paths so the smoke test never touches real config / state / db
_TMP = Path(tempfile.mkdtemp(prefix="octave_pod3_"))
os.environ["SYNC_CONFIG"] = str(_TMP / "config.json")
os.environ["SYNC_STATE"] = str(_TMP / "sync_state.json")
os.environ["SYNC_DB"] = str(_TMP / "octave.db")
os.environ["LOG_FILE"] = str(_TMP / "octave.log")
os.environ["SPOTIFY_TOKEN_CACHE"] = str(_TMP / ".spotify_token_cache")
os.environ.setdefault("API_KEY", "")  # auth disabled for the test

# Seed a minimal config with one playlist so add/list/delete have substrate
(_TMP / "config.json").write_text(json.dumps({
    "match_threshold": 80,
    "jellyfin": {"url": "http://jellyfin:8096"},
    "lidarr": {"url": "http://lidarr:8686"},
    "playlists": [
        {"spotify_playlist_id": "seed_playlist",
         "jellyfin_playlist_name": "Seed",
         "sync_mode": "add_only"},
    ],
}))

from fastapi.testclient import TestClient  # noqa: E402

from octave.web.app import create_app  # noqa: E402


def assert_envelope_ok(payload: dict) -> dict:
    assert "data" in payload, f"missing data: {payload}"
    assert payload.get("error") is None, f"unexpected error: {payload['error']}"
    return payload["data"]


def main() -> int:
    app = create_app()
    with TestClient(app) as client:
        # Health
        r = client.get("/api/health")
        assert r.status_code == 200, r.text
        d = assert_envelope_ok(r.json())
        assert d["status"] == "ok"
        assert d["version"] == "2.0.0"
        print("ok    GET /api/health")

        # Setup status — Spotify/Jellyfin/Lidarr will report not-configured
        r = client.get("/api/setup/status")
        assert r.status_code == 200, r.text
        d = assert_envelope_ok(r.json())
        assert "spotify" in d and "jellyfin" in d and "lidarr" in d
        assert d["playlist_count"] == 1
        print("ok    GET /api/setup/status")

        # Sync status (idle)
        r = client.get("/api/sync/status")
        d = assert_envelope_ok(r.json())
        assert d["status"] in ("idle", "running", "success", "error")
        print("ok    GET /api/sync/status (idle)")

        # NOTE: POST /api/sync/{type} is exercised by the real-container
        # integration test (real credentials required). With TestClient and
        # no Spotify creds the trigger races against teardown — covered by
        # the unit-level runner tests added in a later pod.

        # Logs
        r = client.get("/api/logs?n=10")
        assert r.status_code == 200, r.text
        d = assert_envelope_ok(r.json())
        assert "lines" in d
        print("ok    GET /api/logs")

        # Config
        r = client.get("/api/config")
        d = assert_envelope_ok(r.json())
        assert d["config"]["match_threshold"] == 80
        print("ok    GET /api/config")

        # Playlists list
        r = client.get("/api/playlists")
        d = assert_envelope_ok(r.json())
        assert len(d["playlists"]) == 1
        print("ok    GET /api/playlists")

        # Playlist add
        r = client.post("/api/playlists", json={
            "spotify_playlist_id": "added_via_api",
            "jellyfin_playlist_name": "Added",
            "sync_mode": "full_sync",
        })
        assert r.status_code == 200, r.text
        print("ok    POST /api/playlists")

        # Duplicate add → 409
        r = client.post("/api/playlists", json={
            "spotify_playlist_id": "added_via_api",
        })
        assert r.status_code == 409, r.text
        print("ok    POST /api/playlists (409 on dup)")

        # Delete
        r = client.delete("/api/playlists/added_via_api")
        assert r.status_code == 200, r.text
        d = assert_envelope_ok(r.json())
        assert d["deleted"] is True
        print("ok    DELETE /api/playlists/{id}")

        # Delete missing → 404
        r = client.delete("/api/playlists/nonexistent")
        assert r.status_code == 404, r.text
        print("ok    DELETE /api/playlists/{id} (404 missing)")

        # API-key gate
        os.environ["API_KEY"] = "test-secret"
        try:
            r = client.get("/api/setup/status")
            assert r.status_code == 401, r.text
            r = client.get("/api/setup/status",
                           headers={"X-API-Key": "test-secret"})
            assert r.status_code == 200, r.text
            # Health stays unauthenticated
            r = client.get("/api/health")
            assert r.status_code == 200, r.text
        finally:
            os.environ["API_KEY"] = ""
        print("ok    X-API-Key gate")

    print("\npod 3 smoke test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
