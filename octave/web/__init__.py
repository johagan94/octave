"""FastAPI web layer for octave.

Run with: ``python -m octave.web``
"""

from .app import create_app

__all__ = ["create_app"]
