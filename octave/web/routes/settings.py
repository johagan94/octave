"""Settings API -- GET/PUT /api/settings.

Allows the UI to read and update credentials without editing ``.env``.
Values are persisted to ``settings.json`` in the data directory.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..envelope import ok
from ..settings import ALL_KEYS, get_all_settings, save_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/settings")


@router.get("")
def get_settings():
    """Return all managed settings.  Secrets are masked."""
    return ok(data={"settings": get_all_settings()})


@router.put("")
def update_settings(body: dict):
    """Update one or more settings.  Only known keys are accepted."""
    unknown = [k for k in body if k not in ALL_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail="unknown keys: " + ", ".join(unknown))

    result = save_settings(body)
    return ok(data={"saved": list(body.keys()), "settings": result})
