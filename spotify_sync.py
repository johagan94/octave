#!/usr/bin/env python3
"""Backward-compatible shim.

Historical entry point: ``python3 spotify_sync.py`` keeps working.
The real implementation lives in the ``spotify_sync`` package — invoke
directly with ``python3 -m spotify_sync`` if you prefer.
"""

from spotify_sync.__main__ import main

if __name__ == "__main__":
    main()
