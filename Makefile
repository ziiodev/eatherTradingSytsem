# Aether Trading System — root Makefile.
#
# Make is the lowest-common-denominator task runner; every target degrades
# gracefully if its underlying tool / sub-project hasn't been wired yet so
# contributors get a friendly notice rather than an opaque error.

# Force bash for predictable behavior across distros (Make defaults to /bin/sh).
SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Print a uniformly-formatted "feature not yet wired" notice for missing
# tools or sub-projects, then exit 0 so workflows don't fail on bootstrap.
define notyet
	echo ""; \
	echo "  [skip] $(1)"; \
	echo "         $(2)"; \
	echo "";
endef

# True iff $(1) is available on PATH.
have = command -v $(1) >/dev/null 2>&1

# ---------------------------------------------------------------------------
# Tracked phony targets
# ---------------------------------------------------------------------------
.PHONY: help setup dev db.up db.migrate db.seed db.reset lint test gen.types

help:
	@echo "Aether Trading System — available make targets:"
	@echo ""
	@echo "  setup        Install dev tooling and dependencies (idempotent)."
	@echo "  dev          Start Postgres and run the API and web app in parallel."
	@echo ""
	@echo "  db.up        Start the Postgres container only."
	@echo "  db.migrate   Run Alembic migrations against the dev database."
	@echo "  db.seed      Seed alice / bob users + demo agents/project (dev-only)."
	@echo "  db.reset     Destroy and recreate the Postgres volume, then migrate."
	@echo ""
	@echo "  lint         Run ruff + mypy + pnpm -r lint."
	@echo "  test         Run pytest + pnpm -r test."
	@echo "  gen.types    Regenerate the TypeScript API client from FastAPI schema."
	@echo ""
	@echo "Targets that depend on apps/api or apps/web will print a 'skip' notice"
	@echo "until those sub-projects are scaffolded in later phases."

# ---------------------------------------------------------------------------
# setup: one-shot bootstrap
# ---------------------------------------------------------------------------
setup:
	@echo "==> Installing pre-commit hooks"
	@if $(call have,pre-commit); then \
		pre-commit install; \
	else \
		$(call notyet,pre-commit,Install with: pipx install pre-commit) \
	fi
	@echo ""
	@echo "==> Syncing Python dependencies (apps/api)"
	@if [ -d apps/api ]; then \
		if $(call have,uv); then \
			(cd apps/api && uv sync); \
		else \
			$(call notyet,uv,Install from https://docs.astral.sh/uv/) \
		fi; \
	else \
		$(call notyet,apps/api,Sub-project lands in Phase 3.) \
	fi
	@echo ""
	@echo "==> Installing JS dependencies (pnpm workspace)"
	@if $(call have,pnpm); then \
		pnpm install -r; \
	else \
		$(call notyet,pnpm,Enable with: corepack enable && corepack prepare pnpm@latest) \
	fi
	@echo ""
	@echo "==> Setup complete."

# ---------------------------------------------------------------------------
# dev: run the full local stack
# ---------------------------------------------------------------------------
dev:
	@echo "==> Bringing up Postgres"
	@$(MAKE) --no-print-directory db.up
	@echo ""
	@if [ -d apps/api ] || [ -d apps/web ]; then \
		echo "==> TODO: start apps in parallel once apps/api and apps/web exist."; \
	else \
		$(call notyet,dev,No apps/* present yet. Phases 3 and 4 wire FastAPI and Next.js here.) \
	fi

# ---------------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------------
db.up:
	@if [ ! -f docker-compose.yml ] && [ ! -f compose.yaml ]; then \
		$(call notyet,db.up,No docker-compose file at repo root yet. Phase 2 adds it.) \
	elif ! $(call have,docker); then \
		$(call notyet,db.up,Docker is not installed or not on PATH.) \
	else \
		docker compose up -d postgres; \
	fi

db.migrate:
	@if [ ! -d apps/api ]; then \
		$(call notyet,db.migrate,apps/api does not exist yet. Alembic migrations live there from Phase 3.) \
	elif ! $(call have,uv); then \
		$(call notyet,db.migrate,uv is required to run Alembic. Install from https://docs.astral.sh/uv/.) \
	else \
		(cd apps/api && uv run alembic upgrade head); \
	fi

# db.seed: idempotent dev-only seed (alice / bob + demo agents/project).
# Refuses to run if ENVIRONMENT=prod (see apps/api/scripts/seed_dev.py).
db.seed:
	@if [ ! -f apps/api/scripts/seed_dev.py ]; then \
		$(call notyet,db.seed,apps/api/scripts/seed_dev.py does not exist yet.) \
	elif ! $(call have,uv); then \
		$(call notyet,db.seed,uv is required to run the seed script.) \
	else \
		(cd apps/api && uv run python scripts/seed_dev.py); \
	fi

db.reset:
	@if [ ! -f docker-compose.yml ] && [ ! -f compose.yaml ]; then \
		$(call notyet,db.reset,No docker-compose file yet — nothing to reset.) \
	elif ! $(call have,docker); then \
		$(call notyet,db.reset,Docker is not installed or not on PATH.) \
	else \
		echo "==> Tearing down Postgres (volumes included)"; \
		docker compose down -v; \
		$(MAKE) --no-print-directory db.up; \
		$(MAKE) --no-print-directory db.migrate; \
	fi

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------
lint:
	@echo "==> ruff"
	@if $(call have,ruff); then \
		ruff check .; \
	elif $(call have,uvx); then \
		uvx ruff check .; \
	else \
		$(call notyet,ruff,Install via uv (uv tool install ruff) or pipx.) \
	fi
	@echo ""
	@echo "==> mypy"
	@if $(call have,mypy); then \
		mypy .; \
	elif $(call have,uvx); then \
		uvx mypy .; \
	else \
		$(call notyet,mypy,Install via uv (uv tool install mypy) or pipx.) \
	fi
	@echo ""
	@echo "==> pnpm -r lint"
	@if $(call have,pnpm); then \
		pnpm -r --if-present lint; \
	else \
		$(call notyet,pnpm,Enable with: corepack enable.) \
	fi

test:
	@echo "==> pytest (apps/api)"
	@if [ -d apps/api ]; then \
		(cd apps/api && uv run pytest); \
	else \
		$(call notyet,pytest,apps/api does not exist yet. Phase 3 wires the backend.) \
	fi
	@echo ""
	@echo "==> pnpm -r test"
	@if $(call have,pnpm); then \
		pnpm -r --if-present test; \
	else \
		$(call notyet,pnpm,Enable with: corepack enable.) \
	fi

# ---------------------------------------------------------------------------
# Codegen
# ---------------------------------------------------------------------------
# gen.types runs two best-effort steps. Each one degrades to a friendly
# "skip" notice if its toolchain (uv / pnpm) is missing or the sub-project
# hasn't been wired yet, mirroring the pattern used elsewhere in this
# Makefile. CI is responsible for ensuring both toolchains exist and then
# running `git diff --exit-code` to catch drift.
gen.types:
	@echo "==> Dumping FastAPI OpenAPI schema → apps/api/openapi.json"
	@if [ ! -f apps/api/scripts/dump_openapi.py ]; then \
		$(call notyet,gen.types backend,apps/api/scripts/dump_openapi.py does not exist yet.) \
	elif ! $(call have,uv); then \
		$(call notyet,gen.types backend,uv is required to dump the OpenAPI schema.) \
	else \
		(cd apps/api && uv run python scripts/dump_openapi.py); \
	fi
	@echo ""
	@echo "==> Regenerating TypeScript types → packages/shared-types/src/api.ts"
	@if [ ! -d packages/shared-types ]; then \
		$(call notyet,gen.types ts,packages/shared-types does not exist yet.) \
	elif ! $(call have,pnpm); then \
		$(call notyet,gen.types ts,pnpm is required to run openapi-typescript.) \
	else \
		pnpm --filter @aether/shared-types gen; \
	fi
