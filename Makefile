# MotionMirror command surface. Real scripts only: every target here runs.
SHELL := /bin/bash
COMPOSE := docker compose --env-file .env -f infra/compose/compose.yaml
export JAVA_HOME := $(shell for d in /opt/homebrew/opt/openjdk@21 /usr/lib/jvm/java-21-openjdk-amd64 /usr/lib/jvm/java-21-openjdk-arm64; do [ -d "$$d" ] && { echo $$d; break; }; done)

.DEFAULT_GOAL := help

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t 28

.PHONY: doctor
doctor: ## Report machine, runtime and service health. Changes nothing
	@python3 scripts/doctor.py

.PHONY: bootstrap
bootstrap: ## Create .env with generated secrets and render local config. Downloads no models or data
	@python3 scripts/bootstrap.py
	@pnpm install --frozen-lockfile=false --silent 2>/dev/null || true

.PHONY: up-mock
up-mock: bootstrap ## Start the full stack with the mock worker (any CPU machine)
	$(COMPOSE) --profile lite up -d --build --wait

.PHONY: up-mac
up-mac: up-mock ## Same as up-mock; every service runs natively on arm64

.PHONY: down
down: ## Stop services and keep all data
	$(COMPOSE) --profile lite --profile full --profile gpu down

.PHONY: logs
logs: ## Follow service logs
	$(COMPOSE) --profile lite --profile full --profile gpu logs -f --tail=100

.PHONY: destroy-all-local-data
destroy-all-local-data: ## DESTRUCTIVE: delete local databases, broker state and stored media. Asks first
	@read -p "Delete all local databases and media? Type 'delete': " answer; [ "$$answer" = delete ]
	$(COMPOSE) --profile lite --profile full --profile gpu down -v

.PHONY: test-tools
test-tools: ## Unit tests for repository tooling
	uv run --with pytest --no-project pytest tests/tools -q

.PHONY: datasets-fetch
datasets-fetch: ## Download a registered dataset. Needs DATASET=<id>
	cd training && uv run mm-train datasets-fetch --dataset $(DATASET)

.PHONY: datasets-validate
datasets-validate: ## Verify a downloaded dataset against its checksums. Needs DATASET=<id>
	cd training && uv run mm-train datasets-validate --dataset $(DATASET)
