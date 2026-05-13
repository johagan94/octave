# ---------------------------------------------------------------------------
# Build stage — install Python deps into a virtualenv we can copy out
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Runtime stage — slim image, non-root user, only the venv + app
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    SYNC_MODE=web \
    SYNC_CONFIG=/app/config/config.json \
    SYNC_STATE=/app/data/sync_state.json \
    LOG_FILE=/app/logs/octave.log \
    LOG_LEVEL=INFO \
    SPOTIFY_TOKEN_CACHE=/app/data/.spotify_token_cache \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8000 \
    SYNC_ON_STARTUP=false \
    TZ=UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid app --create-home --shell /bin/bash app

# Bring the prebuilt virtualenv across
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Application source
COPY --chown=app:app spotify_sync/ /app/spotify_sync/
COPY --chown=app:app config.example.json /app/config.example.json
COPY --chown=app:app entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/config /app/data /app/logs \
    && chown -R app:app /app

USER app

EXPOSE 8000 8888

# tini reaps zombies, forwards signals — important for graceful shutdown
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${WEB_PORT}/api/health || exit 1
