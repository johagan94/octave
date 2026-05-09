#!/usr/bin/env bash
# Container entrypoint for spotify_sync.
#
# Modes (selected via $SYNC_MODE):
#   web      → run the FastAPI web server (default; recommended)
#   oneshot  → run a single sync cycle and exit (cron-friendly)
#
# Volume mounts expected:
#   /app/config  → user-editable config.json (and per-concern files later)
#   /app/data    → sync_state.json, .spotify_token_cache, run history DB
#   /app/logs    → spotify_sync.log (rotated by the host or a sidecar)
#
# All paths inside the package are env-overridable so we don't hard-code them.
set -euo pipefail

CONFIG_DIR=${CONFIG_DIR:-/app/config}
DATA_DIR=${DATA_DIR:-/app/data}
LOG_DIR=${LOG_DIR:-/app/logs}

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR"

# ---------------------------------------------------------------------------
# First-run bootstrap: seed config.json from the bundled example if absent.
# ---------------------------------------------------------------------------
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
    if [[ -f /app/config.example.json ]]; then
        echo "[entrypoint] No config.json found in $CONFIG_DIR — seeding from config.example.json"
        cp /app/config.example.json "$CONFIG_DIR/config.json"
    else
        echo "[entrypoint] WARNING: no config.json and no config.example.json — sync will fail"
    fi
fi

# ---------------------------------------------------------------------------
# Token cache permissions: contains a Spotify refresh token. Keep it 0600.
# ---------------------------------------------------------------------------
TOKEN_CACHE=${SPOTIFY_TOKEN_CACHE:-$DATA_DIR/.spotify_token_cache}
if [[ -f "$TOKEN_CACHE" ]]; then
    chmod 600 "$TOKEN_CACHE" || true
fi

# ---------------------------------------------------------------------------
# Resolve mode
# ---------------------------------------------------------------------------
MODE=${SYNC_MODE:-web}

echo "[entrypoint] mode=$MODE  config=$SYNC_CONFIG  state=$SYNC_STATE  log=$LOG_FILE"

case "$MODE" in
    oneshot)
        exec python -m spotify_sync
        ;;
    web)
        # SYNC_ON_STARTUP is honored by the web app itself, not the entrypoint.
        exec python -m spotify_sync.web
        ;;
    *)
        echo "[entrypoint] Unknown SYNC_MODE='$MODE'. Valid: web, oneshot" >&2
        exit 64
        ;;
esac
