.PHONY: help up down build logs ps sh test test-unit test-cov test-e2e lint fmt \
        typecheck ui ui-dev ui-check demo-warm demo-check migrate revision drift seed seed-clean demo screen progress venv clean

SHELL := /bin/bash
COMPOSE := docker compose
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:  ## Show available targets
	@grep -E '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

# ── local dev (no Docker needed for unit tests) ─────────────────────────────
venv:  ## Create .venv and install backend deps for local testing
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt -r backend/requirements-dev.txt
	@echo "venv ready — run 'make test'"

test: test-unit  ## Run the fast test suite (no Docker, no network)

test-unit:  ## Unit + property + golden + integration tests, no network
# The marker filter is not optional. Passing directories alone still runs
# anything marked `network` inside them -- `TestAgainstTheRealBucket` lives in
# golden/, so `make test` reached out to S3 and failed on a machine with no
# network, while this target's own summary line promises it does not.
	$(PY) -m pytest backend/app/tests/unit backend/app/tests/property \
	                backend/app/tests/golden backend/app/tests/integration \
	                -m "not network and not e2e" -q

test-e2e:  ## Browser test of the running stack (skips if it is not up)
	$(PY) -m pytest backend/app/tests/e2e -q -rs

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
# Run from backend/, where [tool.mypy] lives. mypy looks for its config in the
# working directory rather than walking up from the target paths (unlike ruff and
# black), so invoking it from the repo root silently dropped the config and
# reported 20 phantom "missing library stubs" errors.
	cd backend && $(CURDIR)/$(PY) -m mypy app/core app/services

# ── frontend ───────────────────────────────────────────────────────────────
ui:  ## Install deps and build the production bundle
	cd frontend && npm install --no-audit --no-fund && npm run build

ui-dev:  ## Vite dev server on :5173, proxying /api to the running API
	cd frontend && npm run dev

ui-check:  ## Typecheck the frontend
	cd frontend && npx tsc --noEmit

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

drift:  ## Fail if the models and the migrated schema disagree
	$(COMPOSE) exec api alembic check

revision:  ## Create a migration: make revision M="add x"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(M)"

seed:  ## Seed admin boundaries + village name index: make seed [STATE=chhattisgarh]
	$(COMPOSE) exec api python -m scripts.seed_villages --state $(or $(STATE),chhattisgarh)

seed-clean:  ## Wipe and re-seed from scratch (re-downloads if --refresh added)
	$(COMPOSE) exec -T postgis psql -U contour -d contour -c 'truncate villages, admin_areas cascade'
	$(MAKE) seed

API ?= http://localhost:8000

demo:  ## Analyse the bundled sample contour map against a running API
	./scripts/demo_contour.sh

report:  ## Build docs/REPORT.html + REPORT.pdf from captured API output
	.venv/bin/python tools/report.py --pdf

report-capture:  ## Re-capture the API output the report quotes (needs a running API)
	@mkdir -p docs/report/assets
	curl -s -X POST $(API)/api/v1/analyzeContour \
	  -F "file=@contours_1m.kml" -F "max_sites=5" -o docs/report/assets/analysis.json
	curl -s $(API)/openapi.json -o docs/report/assets/openapi.json
	@echo "captured; now run: make report"

demo-warm:  ## Fill the provider caches, then prove DEMO_MODE runs offline
	$(COMPOSE) exec api python -m scripts.warm_demo

demo-check:  ## Verify the warm cache without touching the network
	$(COMPOSE) exec api python -m scripts.warm_demo --verify

screen:  ## Screen candidate test villages against the M0-15 criteria
	$(PY) scripts/screen_sites.py

# ── tracking ───────────────────────────────────────────────────────────────
progress:  ## Report plan progress and next startable tasks
	python3 progress.py

clean:  ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
