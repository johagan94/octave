"""Config loading. Non-secret settings come from config.json; credentials
come from environment variables (loaded from .env if present)."""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Load .env once at import time so os.environ is populated before
# anything else reads it. Safe to call multiple times.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded (missing env, bad file)."""


def _require_env(key: str) -> str:
    """Return an env var or raise ConfigError. The CLI entry point converts
    this into a clean exit; the web runner reports it as a sync failure."""
    val = os.environ.get(key, "").strip()
    if not val:
        msg = (
            f"Missing required environment variable: {key}. "
            f"Add it to your .env file:  {key}=your_value_here"
        )
        log.error(msg)
        raise ConfigError(msg)
    return val


def config_path() -> Path:
    """Resolve the active config.json path (env override or cwd default)."""
    return Path(os.environ.get("SYNC_CONFIG", "config.json"))


def load_config() -> dict:
    """Load config.json and inject credentials from the environment."""
    path = config_path()
    if not path.exists():
        msg = f"Config file not found: {path}"
        log.error(msg)
        raise ConfigError(msg)
    with path.open() as fh:
        cfg = json.load(fh)

    cfg.setdefault("spotify", {})
    cfg["spotify"]["client_id"] = _require_env("SPOTIFY_CLIENT_ID")
    cfg["spotify"]["client_secret"] = _require_env("SPOTIFY_CLIENT_SECRET")
    cfg["spotify"]["redirect_uri"] = os.environ.get(
        "SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback"
    )

    cfg.setdefault("jellyfin", {})
    cfg["jellyfin"]["api_key"] = _require_env("JELLYFIN_API_KEY")
    cfg["jellyfin"]["user_id"] = _require_env("JELLYFIN_USER_ID")
    if os.environ.get("JELLYFIN_URL"):
        cfg["jellyfin"]["url"] = os.environ["JELLYFIN_URL"]

    cfg.setdefault("lidarr", {})
    cfg["lidarr"]["api_key"] = _require_env("LIDARR_API_KEY")
    if os.environ.get("LIDARR_URL"):
        cfg["lidarr"]["url"] = os.environ["LIDARR_URL"]

    return cfg
