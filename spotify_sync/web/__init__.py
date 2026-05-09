"""FastAPI web layer for spotify_sync.

Run with: ``python -m spotify_sync.web``
"""

from .app import create_app

__all__ = ["create_app"]
