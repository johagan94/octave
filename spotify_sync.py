"""Backward-compatible shim.

The real implementation lives in the ``octave`` package -- invoke
directly with ``python3 -m octave`` if you prefer.
"""
from octave import __version__
from octave.__main__ import run_sync

__all__ = ["run_sync", "__version__"]
