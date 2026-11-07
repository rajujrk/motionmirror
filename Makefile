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

.PHONY: contracts
contracts: ## Regenerate OpenAPI and the TypeScript client from the running API
	$(COMPOSE) --profile lite exec -T api wget -qO- http://127.0.0.1:8081/v3/api-docs > packages/contracts/openapi.json
	pnpm --filter @motionmirror/contracts generate

.PHONY: test
test: test-tools ## Unit and contract tests. Needs no containers or downloaded weights
	cd apps/api && ./mvnw -q test -Dtest='!*IT'
	cd services/ml-worker && uv run --extra wan --group dev pytest tests/unit tests/contract -q
	cd training && uv run --group dev pytest tests -q
	pnpm --filter @motionmirror/web test
	pnpm --filter @motionmirror/web typecheck
	pnpm --filter @motionmirror/web lint

.PHONY: test-integration
test-integration: ## Real PostgreSQL, RabbitMQ, Redis and storage through Testcontainers
	cd apps/api && ./mvnw -q test -Dtest='*IT'
	cd services/ml-worker && uv run --extra wan --group dev pytest tests/integration -q

.PHONY: test-e2e
test-e2e: ## Browser journey through the running stack. Run make up-mac first
	pnpm --filter @motionmirror/e2e test

.PHONY: models-download
models-download: ## Download pinned weights. PROFILE=preprocess|preprocess-gpu|wan|mac-spike. Explicit only
	cd services/ml-worker && uv run --extra preprocess python -m motionmirror.cli.models --profile $(PROFILE)

.PHONY: up-mac-native
up-mac-native: bootstrap ## Containers for everything except workers, for use with worker-native-mac
	$(COMPOSE) --profile lite stop worker-mock
	$(COMPOSE) -f infra/compose/native-worker.yaml --profile lite up -d --build --wait

.PHONY: worker-native-mac
worker-native-mac: ## Real masks and poses on this Mac, labelled experimental. Run up-mac-native first
	python3 scripts/worker_native.py

.PHONY: worker-check
worker-check: ## Prove the native workers can reach what they need, and only that. Loads no model
	python3 scripts/worker_native.py --check

.PHONY: build-worker-amd64
build-worker-amd64: ## Build the CUDA worker image for linux/amd64 and check its architecture
	docker buildx build --platform linux/amd64 --load -f services/ml-worker/Dockerfile.cuda -t $(or $(IMAGE),motionmirror/worker-wan:dev) .
	@test "$$(docker image inspect $(or $(IMAGE),motionmirror/worker-wan:dev) --format '{{.Architecture}}')" = amd64 && echo "Architecture is amd64"

.PHONY: up-gpu
up-gpu: bootstrap ## Full stack with the real worker on a CUDA machine
	MM_GENERATE_BACKEND=wan-animate MM_PREPROCESS_BACKEND=real-preprocess $(COMPOSE) --profile gpu up -d --build --wait

.PHONY: smoke-gpu
smoke-gpu: ## GPU fitness and connectivity. With CLIP, PHOTO and MARK it also runs one short real preview
	$(COMPOSE) --profile gpu run --rm --no-deps --entrypoint python worker-wan -m motionmirror.cli.gpu_check
	$(COMPOSE) --profile gpu run --rm --entrypoint python worker-wan -m motionmirror.cli.worker_check
	@if [ -n "$(CLIP)" ]; then $(MAKE) benchmark-gpu PRESET=preview; else echo "Fitness only. Pass CLIP=/media/x.mp4 PHOTO=/media/y.png MARK=x,y to run the model"; fi

.PHONY: benchmark-gpu
benchmark-gpu: ## One measured run. Files go in .data/benchmark. CLIP=/media/x.mp4 PHOTO=/media/y.png MARK=x,y PRESET=standard
	$(COMPOSE) --profile gpu run --rm --no-deps --entrypoint python worker-wan -m motionmirror.cli.spike_infer \
		--source $(CLIP) --reference $(PHOTO) --mark $(MARK) --preset $(or $(PRESET),standard) \
		--out /media/runs/$(or $(PRESET),standard)-$$(date +%Y%m%d-%H%M%S)

.PHONY: spike-mac
spike-mac: ## Measure tracking and pose on this Mac. CLIP_DIR holds video.mp4 and dataset_mask.mp4
	cd services/ml-worker && uv run --extra wan --extra preprocess python -m motionmirror.cli.spike_mac --clip-dir $(abspath $(CLIP_DIR)) --out $(abspath .data/runs/spike)

.PHONY: test-tools
test-tools: ## Unit tests for repository tooling
	uv run --with pytest --no-project pytest tests/tools -q

.PHONY: datasets-fetch
datasets-fetch: ## Download a registered dataset. Needs DATASET=<id>
	cd training && uv run mm-train datasets-fetch --dataset $(DATASET)

.PHONY: samples-build
samples-build: ## Build training samples with real pose estimation. DATASET=<id> LIMIT=<n>
	cd training && uv run mm-train samples-build --dataset $(DATASET) $(if $(LIMIT),--limit $(LIMIT))

.PHONY: datasets-validate
datasets-validate: ## Verify a downloaded dataset against its checksums. Needs DATASET=<id>
	cd training && uv run mm-train datasets-validate --dataset $(DATASET)
