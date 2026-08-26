.PHONY: help up down build logs ps sh test test-unit test-cov lint fmt typecheck \
        migrate revision seed demo progress venv clean

SHELL := /bin/bash
COMPOSE := docker compose
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

# ── local dev (no Docker needed for unit tests) ─────────────────────────────
venv:  ## Create .venv and install backend deps for local testing
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt -r backend/requirements-dev.txt
	@echo "venv ready — run 'make test'"

test: test-unit  ## Run the fast test suite (no Docker, no network)

test-unit:  ## Unit + property + golden tests
	$(PY) -m pytest backend/app/tests/unit backend/app/tests/property \
	                backend/app/tests/golden backend/app/tests/integration -q

test-cov:  ## Tests with coverage report on services/ and core/
	$(PY) -m pytest backend/app/tests -q \
	    --cov=backend/app/core --cov=backend/app/services \
	    --cov-report=term-missing

lint:  ## ruff + black --check
	$(PY) -m ruff check backend/app
	$(PY) -m black --check backend/app

fmt:  ## Auto-format
	$(PY) -m ruff check --fix backend/app
	$(PY) -m black backend/app

typecheck:  ## mypy on the domain layer
	$(PY) -m mypy backend/app/core backend/app/services

# ── docker stack ───────────────────────────────────────────────────────────
build:  ## Build all images
	$(COMPOSE) build

up:  ## Start the stack
	$(COMPOSE) up -d --build
	@echo "api      -> http://localhost:8000/api/v1/health"
	@echo "docs     -> http://localhost:8000/docs"
	@echo "frontend -> http://localhost:8080"

down:  ## Stop the stack
	$(COMPOSE) down

logs:  ## Tail all logs
	$(COMPOSE) logs -f --tail=100

ps:  ## Show service status
	$(COMPOSE) ps

sh:  ## Shell into the api container
	$(COMPOSE) exec api bash

# ── database ───────────────────────────────────────────────────────────────
migrate:  ## Apply migrations
	$(COMPOSE) exec api alembic upgrade head

revision:  ## Create a migration: make revision M="add x"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(M)"

seed:  ## Load SHRUG village polygons (M0-11)
	$(COMPOSE) exec api python -m scripts.seed_villages

demo:  ## Warm the cache for the test villages, then run offline (M7-10)
	$(COMPOSE) exec api python -m scripts.warm_cache

# ── tracking ───────────────────────────────────────────────────────────────
progress:  ## Report plan progress and next startable tasks
	python3 progress.py

clean:  ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
