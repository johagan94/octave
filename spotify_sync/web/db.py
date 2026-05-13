"""SQLite persistence for run history and per-playlist last-sync.

Used for *new* state introduced by the web layer. The legacy
``sync_state.json`` (Lidarr request state machine) is unchanged — it
predates this layer and is the source of truth for cross-run Lidarr
workflows.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    matched INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    albums_requested INTEGER DEFAULT 0,
    waiting_lidarr INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_started ON sync_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS playlist_state (
    spotify_id TEXT PRIMARY KEY,
    last_synced_at TEXT,
    last_matched INTEGER,
    last_missing INTEGER
);

CREATE TABLE IF NOT EXISTS sync_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    spotify_id TEXT NOT NULL,
    playlist_name TEXT,
    status TEXT NOT NULL,
    matched INTEGER DEFAULT 0,
    missing INTEGER DEFAULT 0,
    albums_requested INTEGER DEFAULT 0,
    waiting_lidarr INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_items_run ON sync_items(run_id);
"""

_LOCK = threading.Lock()


def db_path() -> Path:
    return Path(os.environ.get("SYNC_DB", "/app/data/spotify_sync.db"))


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db() -> None:
    """Create tables if absent. Idempotent — safe to call on every startup."""
    with _LOCK, _connect() as con:
        con.executescript(_SCHEMA)


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    """Yield a cursor backed by a one-off connection.

    SQLite + WAL handles concurrent reads fine; we serialize writes via
    `_LOCK` for callers that need atomicity across multiple statements.
    """
    con = _connect()
    try:
        yield con.cursor()
    finally:
        con.close()


# ── Run history ───────────────────────────────────────────────────────

def insert_run(
    type_: str,
    started_at: datetime,
    status: str = "running",
) -> int:
    with _LOCK, _connect() as con:
        cur = con.execute(
            "INSERT INTO sync_runs (type, status, started_at) VALUES (?, ?, ?)",
            (type_, status, started_at.isoformat()),
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    status: str,
    finished_at: datetime,
    matched: int = 0,
    missing: int = 0,
    albums_requested: int = 0,
    waiting_lidarr: int = 0,
    error: Optional[str] = None,
) -> None:
    with _LOCK, _connect() as con:
        con.execute(
            """UPDATE sync_runs
               SET status = ?, finished_at = ?, matched = ?, missing = ?,
                   albums_requested = ?, waiting_lidarr = ?, error = ?
               WHERE id = ?""",
            (status, finished_at.isoformat(), matched, missing,
             albums_requested, waiting_lidarr, error, run_id),
        )


def latest_run(type_: Optional[str] = None) -> Optional[sqlite3.Row]:
    with cursor() as cur:
        if type_:
            cur.execute(
                "SELECT * FROM sync_runs WHERE type = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (type_,),
            )
        else:
            cur.execute("SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT 1")
        return cur.fetchone()


def get_run_history(limit: int = 10) -> list[sqlite3.Row]:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()


def insert_sync_item(
    run_id: int,
    spotify_id: str,
    playlist_name: str,
    status: str,
    matched: int = 0,
    missing: int = 0,
    albums_requested: int = 0,
    waiting_lidarr: int = 0,
    error: Optional[str] = None,
) -> None:
    with _LOCK, _connect() as con:
        con.execute(
            """INSERT INTO sync_items
               (run_id, spotify_id, playlist_name, status,
                matched, missing, albums_requested, waiting_lidarr, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, spotify_id, playlist_name, status,
             matched, missing, albums_requested, waiting_lidarr, error),
        )


def get_sync_items(run_id: int) -> list[sqlite3.Row]:
    with cursor() as cur:
        cur.execute(
            "SELECT * FROM sync_items WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        return cur.fetchall()
