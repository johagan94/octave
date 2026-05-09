"""Logs endpoints: tail (one-shot) + stream (SSE)."""

from __future__ import annotations

import asyncio
import os
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from ..envelope import ok, err
from ..models import LogTail

router = APIRouter()


def _log_path() -> Path:
    return Path(os.environ.get("LOG_FILE", "spotify_sync.log"))


@router.get("/logs")
def get_logs(n: int = Query(default=200, ge=1, le=10000)):
    path = _log_path()
    if not path.exists():
        return ok(LogTail(lines=[], file=str(path)))
    try:
        # Efficient last-N-lines without loading the whole file: deque + iteration
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            lines = list(deque(fh, maxlen=n))
        return ok(LogTail(lines=[ln.rstrip("\n") for ln in lines],
                          file=str(path)))
    except OSError as exc:
        return err("logs_unreadable", str(exc), status=500)


async def _tail(path: Path, poll_interval: float = 0.5):
    """Async generator that yields new log lines as they arrive.

    Starts at end-of-file; each iteration yields one already-flushed line.
    Re-opens the file on rotation (inode change).
    """
    inode = None
    fh = None
    while True:
        try:
            stat = path.stat()
        except FileNotFoundError:
            await asyncio.sleep(poll_interval)
            continue
        if fh is None or stat.st_ino != inode:
            if fh is not None:
                fh.close()
            fh = path.open("r", encoding="utf-8", errors="replace")
            fh.seek(0, 2)  # tail from EOF
            inode = stat.st_ino

        line = fh.readline()
        if line:
            yield line.rstrip("\n")
        else:
            await asyncio.sleep(poll_interval)


@router.get("/logs/stream")
async def stream_logs():
    """SSE stream of new log lines. Connect with EventSource() in JS."""
    path = _log_path()

    async def event_gen():
        async for line in _tail(path):
            yield {"event": "log", "data": line}

    return EventSourceResponse(event_gen())
