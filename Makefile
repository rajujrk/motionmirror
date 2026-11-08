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

.PHONY: observability-up
observability-up: bootstrap ## Metrics, traces and dashboards on the mock stack. Grafana: localhost:53000
	MM_TRACING_ENABLED=true MM_WORKER_OTLP_ENDPOINT=http://tempo:4318/v1/traces $(COMPOSE) --profile full up -d --build --wait

.PHONY: scan
scan: ## Secrets, code and dependency scans with pinned tools. Exceptions live in .trivyignore and .semgrepignore
	docker run --rm -v "$(CURDIR):/repo:ro" zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f git --no-banner --redact --config /repo/.gitleaks.toml /repo
	docker run --rm -v "$(CURDIR):/repo:ro" -w /repo semgrep/semgrep:1.184.0@sha256:cc1b68266f267878b39739373ea9985b9a78e5d64497dd875c8de100b22695ed semgrep scan --metrics=off --error --severity=ERROR --config p/security-audit --config p/secrets --config p/owasp-top-ten .
	cd apps/api && ./mvnw -q -B dependency:go-offline
	docker run --rm -v "$(CURDIR):/repo:ro" -v mm-trivy-cache:/root/.cache -v "$(HOME)/.m2:/root/.m2:ro" aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb fs --offline-scan --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed --ignorefile /repo/.trivyignore --skip-dirs .data --skip-dirs .models --skip-dirs node_modules --skip-dirs .venv /repo

.PHONY: test-security
test-security: scan ## Scanners plus authorization and upload abuse tests
	cd apps/api && ./mvnw -q test -Dtest='AccessControlIT,UploadIT,AdapterLifecycleIT,DeletionIT,ObservabilityIT,WorkerTokenValidationTest,StartupChecksTest'

.PHONY: test-load
test-load: ## Bounded control-plane load on mock jobs, with no GPU. Needs MM_SESSION_COOKIE, MM_CSRF_TOKEN, MM_PROJECT_ID, MM_GENERATE_BODY
	docker run --rm -i --add-host=host.docker.internal:host-gateway -e MM_SESSION_COOKIE -e MM_CSRF_TOKEN -e MM_PROJECT_ID -e MM_GENERATE_BODY grafana/k6:1.8.2@sha256:00fdcebafbc86cb0137d429529b1caa401a02e09025f02fc74866fa6e2b004d8 run - < tests/load/control-plane.js

.PHONY: test-failure-recovery
test-failure-recovery: ## Kill, restart, freeze and outage through the running stack, plus lease and outbox tests
	cd apps/api && ./mvnw -q test -Dtest='LeaseIT,OutboxIT'
	cd services/ml-worker && uv run --extra wan --group dev pytest tests/unit/test_consumer.py tests/unit/test_runner.py -q
	$(COMPOSE) -f infra/compose/recovery-test.yaml --profile lite up -d --build --wait
	MM_E2E_RECOVERY=1 pnpm --filter @motionmirror/e2e exec playwright test recovery; code=$$?; \
		$(COMPOSE) --profile lite up -d --wait; exit $$code

.PHONY: models-download
models-download: ## Download pinned weights. PROFILE=preprocess|preprocess-gpu|wan|mac-spike. Explicit only
	cd services/ml-worker && uv run --extra preprocess python -m motionmirror.cli.models --profile $(PROFILE)

.PHONY: up-mac-native
up-mac-native: bootstrap ## Containers for everything except workers, for use with worker-native-mac
	$(COMPOSE) --profile lite stop worker-mock worker-validate
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
spike-mac: ## Measure tracking and pose on this Mac. CLIP_DIR holds video.mp4 and dataset_mask.mp4. ONLY=gguf for the 14B attempt
	cd services/ml-worker && uv run --extra wan --extra preprocess python -m motionmirror.cli.spike_mac --clip-dir $(abspath $(CLIP_DIR)) --out $(abspath .data/runs/spike) $(if $(ONLY),--only $(ONLY))

.PHONY: test-tools
test-tools: ## Unit tests for repository tooling
	PYTHONWARNDEFAULTENCODING=1 uv run --with pytest --with pyyaml --no-project pytest tests/tools -q -W error::EncodingWarning

.PHONY: datasets-fetch
datasets-fetch: ## Download a registered dataset. Needs DATASET=<id>
	cd training && uv run mm-train datasets-fetch --dataset $(DATASET)

.PHONY: train-smoke
train-smoke: ## A real loop on tiny weights, or a short pass on a GPU. PROFILE=cpu-smoke|mac-mps|gpu-cuda
	cd training && uv run mm-train train-smoke --profile $(PROFILE) $(if $(SAMPLES),--samples $(abspath $(SAMPLES)))

.PHONY: train-lora
train-lora: ## The real run, which costs real money. Refuses without CONFIRM=yes and SECONDS_PER_STEP
	cd training && uv run mm-train train-lora --profile $(PROFILE) $(if $(SECONDS_PER_STEP),--seconds-per-step $(SECONDS_PER_STEP)) $(if $(filter yes,$(CONFIRM)),--confirm) $(if $(filter yes,$(TRANSFER_PERMISSION)),--transfer-permission-confirmed) $(if $(filter yes,$(BELOW_TESTED)),--accept-below-tested-hardware)

.PHONY: eval-adapter
eval-adapter: ## Base against adapter on the fixed set. ADAPTER=<run dir>. Needs a CUDA worker for promotion evidence
	cd training && uv run mm-train eval-adapter --adapter $(abspath $(ADAPTER)) $(if $(filter yes,$(NOT_FOR_PROMOTION)),--not-for-promotion)

.PHONY: eval-approve
eval-approve: ## Record a person's review. ADAPTER=<run dir> REVIEWER="Name" FAILED="hands feet"
	cd training && uv run mm-train eval-approve --adapter $(abspath $(ADAPTER)) --reviewer "$(REVIEWER)" $(foreach item,$(FAILED),--failed $(item))

.PHONY: adapter-login
adapter-login: ## Sign in as an administrator for the adapter commands
	cd training && uv run mm-train adapter login

.PHONY: adapter-register
adapter-register: ## Upload and register a trained adapter. ADAPTER=<run dir>
	cd training && uv run mm-train adapter register --adapter $(abspath $(ADAPTER)) $(if $(EVAL),--eval-manifest $(abspath $(EVAL)))

.PHONY: adapter-stage
adapter-stage: ## A new preset revision only administrators can see. ADAPTER=<id> PRESET=<id>
	cd training && uv run mm-train adapter stage --id $(ADAPTER) --preset $(or $(PRESET),standard)

.PHONY: adapter-promote
adapter-promote: ## Make a staged adapter the default. Research adapters are refused. ADAPTER=<id> PRESET=<id>
	cd training && uv run mm-train adapter promote --id $(ADAPTER) --preset $(or $(PRESET),standard)

.PHONY: adapter-rollback
adapter-rollback: ## Return a preset's default to its previous revision. PRESET=<id>
	cd training && uv run mm-train adapter rollback --preset $(PRESET)

.PHONY: reconcile-after-restore
reconcile-after-restore: ## After restoring a database backup, apply recorded deletions again. Run before reopening
	cd training && uv run mm-train adapter reconcile-after-restore

.PHONY: adapter-list
adapter-list: ## List registered adapters
	cd training && uv run mm-train adapter list

.PHONY: samples-build
samples-build: ## Build training samples with real pose estimation. DATASET=<id> LIMIT=<n> CLIPS="<id> <id>"
	cd training && uv run mm-train samples-build --dataset $(DATASET) $(if $(LIMIT),--limit $(LIMIT)) $(foreach clip,$(CLIPS),--clip $(clip))

.PHONY: datasets-validate
datasets-validate: ## Verify a downloaded dataset against its checksums. Needs DATASET=<id>
	cd training && uv run mm-train datasets-validate --dataset $(DATASET)
