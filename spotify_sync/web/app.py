"""FastAPI application factory.

Two routers:
  • ``public_router`` (no auth) — only ``/api/health`` so the Docker
    healthcheck and external monitors work without a key.
  • ``api_router`` (optional X-API-Key auth) — everything else.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..logging_setup import configure_logging
from . import db
from .auth import require_api_key
from .envelope import err
from .models import ApiError, ApiResponse
from .routes import config as config_route
from .routes import health, logs, playlists, setup
from .routes import sync as sync_route
from .runner import runner

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db.init_db()
    runner.load_last_from_db()
    log.info("[web] startup complete; api_key_required=%s",
             bool(os.environ.get("API_KEY", "").strip()))

    if os.environ.get("SYNC_ON_STARTUP", "").lower() == "true":
        log.info("[web] SYNC_ON_STARTUP=true — kicking off initial sync")
        try:
            await runner.trigger("all")
        except HTTPException as exc:
            log.warning("[web] startup sync skipped: %s", exc.detail)
        except Exception:
            log.exception("[web] startup sync failed to enqueue")

    yield
    log.info("[web] shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="spotify_sync",
        version="2.0.0",
        description="Spotify → Jellyfin + Lidarr sync.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    # Standardise HTTPException output into the ApiResponse envelope.
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

    # /api/health — no auth, used by Docker healthcheck
    public_router = APIRouter(prefix="/api")
    public_router.include_router(health.router)
    app.include_router(public_router)

    # All other /api/* routes — gated by optional X-API-Key
    api_router = APIRouter(prefix="/api", dependencies=[Depends(require_api_key)])
    api_router.include_router(setup.router)
    api_router.include_router(sync_route.router)
    api_router.include_router(logs.router)
    api_router.include_router(config_route.router)
    api_router.include_router(playlists.router)
    app.include_router(api_router)

    # Frontend SPA — serve index.html + JS + CSS at /
    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    else:
        log.warning("[web] static dir not found at %s — UI will 404", static_dir)

    return app
