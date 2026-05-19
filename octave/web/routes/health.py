"""GET /api/health — unauthenticated, used by Docker healthcheck."""

from __future__ import annotations

import time

from fastapi import APIRouter

from ... import __version__
from ..envelope import ok
from ..models import HealthInfo

router = APIRouter()

_BOOTED_AT = time.time()


@router.get("/health")
def get_health():
    return ok(HealthInfo(
        version=__version__,
        uptime_seconds=int(time.time() - _BOOTED_AT),
    ))
