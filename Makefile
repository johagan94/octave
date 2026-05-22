# Makefile — Octave convenience targets.
# Run `make help` to see all targets.

IMAGE   ?= octave:local
COMPOSE ?= docker compose

.PHONY: help build up down restart logs sync shell lint test clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── Image ────────────────────────────────────────────────────────────────────

build: ## Build (or rebuild) the Docker image
	$(COMPOSE) build

rebuild: ## Force-rebuild the image from scratch (no layer cache)
	$(COMPOSE) build --no-cache

# ── Container lifecycle ──────────────────────────────────────────────────────

up: ## Start the container in the background
	$(COMPOSE) up -d

down: ## Stop and remove the container
	$(COMPOSE) down

restart: ## Restart the container (pick up .env changes without rebuild)
	$(COMPOSE) restart

# ── Observability ─────────────────────────────────────────────────────────────

logs: ## Tail container logs (Ctrl-C to stop)
	$(COMPOSE) logs -f

status: ## Show container status and last sync run
	@echo "=== Container ===" && $(COMPOSE) ps
	@echo ""
	@echo "=== Last sync status ===" \
	  && curl -sf http://localhost:$${WEB_PORT:-8000}/api/sync/status \
	  | python3 -m json.tool 2>/dev/null || echo "(server not reachable)"

# ── One-shot operations ───────────────────────────────────────────────────────

sync: ## Trigger a full sync via the API (container must be running)
	@curl -sf -X POST http://localhost:$${WEB_PORT:-8000}/api/sync/all \
	  -H 'Content-Type: application/json' -d '{}' \
	  | python3 -m json.tool && echo ""

sync-one: ## Trigger sync for a single playlist: make sync-one ID=<spotify_playlist_id>
	@test -n "$(ID)" || (echo "Usage: make sync-one ID=<spotify_playlist_id>" && exit 1)
	@curl -sf -X POST http://localhost:$${WEB_PORT:-8000}/api/sync/all \
	  -H 'Content-Type: application/json' \
	  -d '{"playlist_ids":["$(ID)"]}' \
	  | python3 -m json.tool && echo ""

# ── Development helpers ───────────────────────────────────────────────────────

shell: ## Open a shell inside the running container
	$(COMPOSE) exec octave bash

lint: ## Run ruff linter over the Python package
	@command -v ruff >/dev/null 2>&1 \
	  || { echo "ruff not found — install with: pip install ruff"; exit 1; }
	ruff check octave/

test: ## Run unit tests
	python -m pytest tests

# ── Housekeeping ──────────────────────────────────────────────────────────────

clean: ## Remove __pycache__ and .pyc files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name '*.pyc' -delete 2>/dev/null; true

perms: ## Fix volume dir ownership (run once after first docker compose up on Linux)
	sudo chown -R 1000:1000 ./config ./data ./logs
