"""Pydantic models for the web layer.

Locked contracts — see HANDOFF.md before changing shapes; the frontend
is built against these.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

SyncType = Literal["playlists", "all"]
SyncStatus = Literal["running", "success", "error", "idle"]


class ApiError(BaseModel):
    code: str
    message: str
    details: Optional[dict] = None


class ApiResponse(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[ApiError] = None


class SyncRun(BaseModel):
    type: SyncType = "all"
    status: SyncStatus = "idle"
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current: int = 0
    total: int = 0
    error: Optional[str] = None
    matched: int = 0
    missing: int = 0
    albums_requested: int = 0


class IntegrationStatus(BaseModel):
    configured: bool = False
    reachable: bool = False
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    detail: Optional[dict] = None


class SetupStatus(BaseModel):
    spotify: IntegrationStatus
    jellyfin: IntegrationStatus
    lidarr: IntegrationStatus
    config_loaded: bool = False
    playlist_count: int = 0


class PlaylistEntry(BaseModel):
    """Mirrors one entry of `config["playlists"]`."""

    spotify_playlist_id: str
    jellyfin_playlist_name: Optional[str] = None
    sync_mode: Literal["add_only", "full_sync", "rebuild"] = "add_only"


class HealthInfo(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    uptime_seconds: int


class LogTail(BaseModel):
    lines: list[str]
    file: Optional[str] = None
    truncated: bool = False


class ConfigPayload(BaseModel):
    """Wraps the raw config.json so the API has a stable envelope shape."""

    config: dict[str, Any] = Field(default_factory=dict)


class DeleteResult(BaseModel):
    deleted: bool
