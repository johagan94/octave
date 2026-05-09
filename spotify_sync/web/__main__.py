"""``python -m spotify_sync.web`` — uvicorn entry point.

Honors ``WEB_HOST`` (default 0.0.0.0) and ``WEB_PORT`` (default 8000)
env vars set by the Docker entrypoint.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8000"))
    log_level = os.environ.get("LOG_LEVEL", "info").lower()
    uvicorn.run(
        "spotify_sync.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level,
        access_log=False,  # we have our own logging
    )


if __name__ == "__main__":
    main()
