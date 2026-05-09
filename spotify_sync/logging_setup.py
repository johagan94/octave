"""Logging configuration. Called once from the entry point; library modules
just call ``logging.getLogger(__name__)`` and let the application configure."""

import logging
import os
import sys
from pathlib import Path


def configure_logging(log_path: Path | None = None) -> None:
    """Configure root logger with stdout + optional file handler.

    Honors ``LOG_LEVEL`` env var (default INFO) and ``LOG_FILE`` env var
    (default ``spotify_sync.log`` in the current working dir).
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    if log_path is None:
        log_path = Path(os.environ.get("LOG_FILE", "spotify_sync.log"))

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_path))
    except OSError as exc:
        # Read-only filesystem or permission error — keep stdout only
        print(f"Warning: could not open log file {log_path}: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,  # allow re-configuration if called twice
    )
