"""FastAPI application factory.

Two routers:
  - ``public_router`` (no auth) -- only ``/api/health`` so the Docker
    healthcheck and external monitors work without a key.
  - ``api_router`` (optional X-API-Key auth) -- everything else.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..logging_setup import configure_logging
from . import db
from .auth import require_auth
from .models import ApiError, ApiResponse
from .routes import config as config_route
from .routes import health, logs, playlists, setup
from .routes import sync as sync_route
from .routes import settings as settings_route
from .routes import spotify_auth as spotify_auth_route
from .routes.spotify_auth import callback_router as _spotify_callback_router
from .routes import discover as discover_route
from .runner import runner

log = logging.getLogger(__name__)

_DEFAULT_CRON = "0 2 * * *"  # 02:00 UTC every day


def _make_scheduler(cron: str):
    """Build and return an AsyncIOScheduler with the sync job wired up.

    Returns None if APScheduler is not installed (graceful degradation).
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("[web] apscheduler not installed -- scheduled sync disabled")
        return None

    tz = os.environ.get("TZ", "UTC")
    scheduler = AsyncIOScheduler(timezone=tz)

    async def _scheduled_sync():
        log.info("[scheduler] cron fired -- triggering sync")
        try:
            await runner.trigger("all")
        except HTTPException as exc:
            log.warning("[scheduler] sync skipped: %s", exc.detail)
        except Exception:
            log.exception("[scheduler] sync failed to start")
        finally:
            _update_next_run(scheduler, cron)

    try:
        trigger = CronTrigger.from_crontab(cron, timezone=tz)
    except Exception as exc:
        log.error("[web] invalid SYNC_SCHEDULE %r: %s -- scheduler disabled", cron, exc)
        return None

    scheduler.add_job(_scheduled_sync, trigger, id="main_sync", replace_existing=True)
    return scheduler


def _update_next_run(scheduler, cron: str) -> None:
    """Push the next_run_at time from the scheduler into the runner."""
    try:
        job = scheduler.get_job("main_sync")
        next_fire = job.next_run_time if job else None
        runner.set_schedule(cron, next_fire)
        log.debug("[scheduler] next run at %s", next_fire)
    except Exception as exc:
        log.warning("[scheduler] could not read next_run_time: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db.init_db()
    runner.load_last_from_db()
    log.info("[web] startup complete; auth_enabled=%s",
             bool(_get_setting("AUTH_PASSWORD") if callable(_get_setting := lambda k: __import__("octave.web.settings", fromlist=["get_setting"]).get_setting(k)) else ""))

    cron = os.environ.get("SYNC_SCHEDULE", "").strip() or _DEFAULT_CRON
    scheduler = _make_scheduler(cron)
    if scheduler is not None:
        scheduler.start()
        _update_next_run(scheduler, cron)
        log.info("[web] scheduler started; cron=%r next=%s", cron, runner.status().next_run_at)
    else:
        log.info("[web] scheduled sync disabled (no valid SYNC_SCHEDULE or apscheduler missing)")

    if os.environ.get("SYNC_ON_STARTUP", "").lower() == "true":
        log.info("[web] SYNC_ON_STARTUP=true -- kicking off initial sync")
        try:
            await runner.trigger("all")
        except HTTPException as exc:
            log.warning("[web] startup sync skipped: %s", exc.detail)
        except Exception:
            log.exception("[web] startup sync failed to enqueue")

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    log.info("[web] shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Octave",
        version="3.0.0",
        description="Spotify to Jellyfin + Lidarr sync. ListenBrainz and Last.fm enrichment.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(_request: Request, exc: HTTPException):
        body = ApiResponse(
            error=ApiError(code=str(exc.detail or "http_error"),
                           message=str(exc.detail or "")),
        )
        return JSONResponse(status_code=exc.status_code,
                            content=body.model_dump(exclude_none=True))

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        log.exception("unhandled exception in request: %s", exc)
        body = ApiResponse(error=ApiError(code="internal_error",
                                          message=str(exc)))
        return JSONResponse(status_code=500,
                            content=body.model_dump(exclude_none=True))


    # /api/health -- no auth, used by Docker healthcheck
    public_router = APIRouter(prefix="/api")
    public_router.include_router(health.router)
    app.include_router(public_router)

    # All other /api/* routes -- gated by optional X-API-Key
    api_router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
    api_router.include_router(setup.router)
    api_router.include_router(sync_route.router)
    api_router.include_router(logs.router)
    api_router.include_router(config_route.router)
    api_router.include_router(playlists.router)
    api_router.include_router(settings_route.router)
    api_router.include_router(spotify_auth_route.router)
    api_router.include_router(discover_route.router)
    app.include_router(api_router)

    # Spotify OAuth callback -- root-level, no auth, must be before StaticFiles
    # so FastAPI intercepts /callback?code=...&state=... before the SPA does.
    app.include_router(_spotify_callback_router)

    # Frontend SPA -- serve index.html + JS + CSS at /
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:
        log.warning("[web] static dir not found at %s -- UI will 404", static_dir)

    return app
