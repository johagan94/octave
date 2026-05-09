"""Background sync runner.

One ``asyncio.Lock`` per sync type prevents concurrent runs of the same
type (the legacy code had no mutex — two clicks of "Sync now" could race).
Different types may run in parallel — but for now ``playlists`` and
``all`` are aliases doing the same work, so they share a lock at the
caller level.

The actual sync work blocks (Jellyfin library scan, Spotify pagination,
Lidarr API calls). We dispatch via ``asyncio.to_thread`` so the event
loop stays responsive for status polls and SSE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from .. import __main__ as core_main
from . import db
from .models import SyncRun, SyncType

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SyncRunner:
    def __init__(self) -> None:
        # Single lock; ``playlists`` and ``all`` mean the same thing today.
        self._lock = asyncio.Lock()
        self._current: Optional[SyncRun] = None
        self._last: Optional[SyncRun] = None

    def load_last_from_db(self) -> None:
        """Hydrate ``_last`` from the most recent sync_runs row.
        Call once at app startup, after ``db.init_db()``.

        Any run still marked 'running' in the DB was interrupted by a
        container restart — mark it 'error' so the UI doesn't show a
        permanently-stuck progress card and the lock remains free.
        """
        row = db.latest_run()
        if not row:
            return
        try:
            status = row["status"] if row["status"] in ("running", "success", "error", "idle") else "idle"
            finished_at = datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            error = row["error"]

            if status == "running":
                status = "error"
                error = "interrupted by container restart"
                finished_at = _now()
                db.finish_run(row["id"], "error", finished_at, error=error)
                log.warning("[runner] previous run was interrupted; marked as error in DB")

            self._last = SyncRun(
                type=row["type"] if row["type"] in ("playlists", "all") else "all",
                status=status,
                started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                finished_at=finished_at,
                matched=row["matched"] or 0,
                missing=row["missing"] or 0,
                albums_requested=row["albums_requested"] or 0,
                error=error,
            )
        except Exception as exc:
            log.warning("could not hydrate last SyncRun from db: %s", exc)

    # ── Public API ────────────────────────────────────────────────────

    def status(self) -> SyncRun:
        if self._current is not None:
            return self._current
        if self._last is not None:
            return self._last
        return SyncRun(status="idle")

    async def trigger(self, sync_type: SyncType = "all") -> SyncRun:
        """Start a sync if none is running. Returns immediately.

        The actual work proceeds on a worker thread; the returned SyncRun
        has ``status='running'``. Poll ``/api/sync/status`` for updates.

        Raises HTTPException(409) if a sync is already in flight.
        """
        if self._lock.locked():
            raise HTTPException(status_code=409, detail="sync_already_running")

        await self._lock.acquire()
        try:
            run = SyncRun(type=sync_type, status="running", started_at=_now())
            run_id = db.insert_run(run.type, run.started_at, "running")
            self._current = run
            log.info("[runner] sync started type=%s db_id=%d", sync_type, run_id)
            asyncio.create_task(self._execute(run_id))
            return run
        except Exception:
            # Failed to enqueue — release lock so the next attempt isn't blocked
            self._lock.release()
            raise

    # ── Internal ──────────────────────────────────────────────────────

    async def _execute(self, run_id: int) -> None:
        run = self._current
        assert run is not None  # set by trigger()
        try:
            def progress_cb(current: int, total: int) -> None:
                run.current = current
                run.total = total

            totals = await asyncio.to_thread(core_main.run_sync, progress_cb)
            run.matched = totals.get("matched", 0)
            run.missing = totals.get("missing", 0)
            run.albums_requested = totals.get("albums_requested", 0)
            run.status = "success"
            run.finished_at = _now()
            db.finish_run(
                run_id, "success", run.finished_at,
                matched=run.matched, missing=run.missing,
                albums_requested=run.albums_requested,
            )
            log.info("[runner] sync OK in %s",
                     run.finished_at - run.started_at if run.started_at else "?")
        except Exception as exc:
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = _now()
            db.finish_run(run_id, "error", run.finished_at,
                          error=run.error[:500])
            log.exception("[runner] sync failed: %s", exc)
        finally:
            self._last = run
            self._current = None
            self._lock.release()


# Module-level singleton — `app.py` accesses this directly. No DI needed
# for a single-process app.
runner = SyncRunner()


def get_runner() -> SyncRunner:
    return runner
