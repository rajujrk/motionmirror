# Local development

## Quick start, any machine

```bash
make bootstrap      # Creates .env with generated secrets. Downloads no models or data
make up-mac         # The full stack with the mock worker (same as make up-mock)
```

Open <http://localhost:8088> and sign in as `dev.user`, with `MM_DEV_USER_PASSWORD` from `.env`. Generation here is mock, labelled `MOCK / NO AI REPLACEMENT` everywhere. `make down` stops everything and keeps your data. `make destroy-all-local-data` removes it.

## Four ways to run the worker

| Mode | Command | Tracking | Generation |
| --- | --- | --- | --- |
| Mock, in containers | `make up-mock` | Stand-in box | Mock |
| Native on the Mac | `make up-mac-native`, then `make worker-native-mac` | Real, labelled experimental | Mock |
| Local NVIDIA GPU | `make up-gpu` | Real | Real |
| Remote GPU worker | See below | Real | Real |

### Native on the Mac

```bash
make models-download PROFILE=preprocess
make up-mac-native
make worker-check          # Proves identity, broker access and limits, and signed URLs. Loads no model
make worker-native-mac     # Ctrl-C stops it
```

Containers can't reach a host address, the host can't reach a Docker name, and a signed URL only works for one of them, so in this mode no worker runs in a container. To go back: `make down`, then `make up-mock`.

A clip of about 3 seconds takes roughly 4 minutes end to end. `docs/BENCHMARKS.md` has the measurements.

### Local NVIDIA GPU

Needs the NVIDIA driver and Container Toolkit. **Validate the GPU before downloading anything**, then:

```bash
make up-gpu                                     # Starts the stack with the real worker
make smoke-gpu                                  # GPU fitness and connectivity, before any weights
make models-download PROFILE=wan                # About 83 GB
# Put a short clip and a full-body photo in .data/benchmark, then:
make smoke-gpu CLIP=/media/clip.mp4 PHOTO=/media/me.png MARK=0.5,0.5
```

`MARK` is `x,y` in the range 0 to 1, on the dancer. `gpu_check` must pass first: it fails clearly when the driver, PyTorch's CUDA, bf16 or ONNX Runtime's GPU provider is missing.

### Remote GPU worker

The standard way for a Mac to produce real output. A remote worker can't reach `localhost` or Docker names. It needs AMQPS to the broker, HTTPS to the API and token endpoints, and the object store. It doesn't need PostgreSQL or Redis. Two options:

- **A cloud control plane** both the Mac browser and the GPU can reach. Follow `docs/runbooks/CLOUD_GPU_DEPLOYMENT.md`.
- **Your local control plane over a private overlay** such as WireGuard or Tailscale, carrying AMQPS, HTTPS and storage traffic. An HTTP tunnel alone doesn't carry AMQP. Don't expose local services without meaning to.

Either way, run the same checks from the worker's side before loading a model:

```bash
docker run --rm --entrypoint python --env-file worker.env <worker image> -m motionmirror.cli.worker_check
```

Keep the browser, API and worker storage endpoints separate where needed (`MM_S3_PUBLIC_ENDPOINT`, `MM_S3_INTERNAL_ENDPOINT`, `MM_S3_WORKER_ENDPOINT`), so signed URLs are valid from where they're used.

## Hot reload

```bash
docker compose --env-file .env -f infra/compose/compose.yaml --profile lite stop web
pnpm --filter @motionmirror/web dev      # Then use http://localhost:3000
```

For the API: stop the `api` container and run `cd apps/api && ./mvnw spring-boot:run`, with the same environment.

## Windows

Use WSL2 with Docker Desktop's WSL integration. **Keep the repository inside the WSL filesystem** (for example `~/motionmirror`), not under `/mnt/c`, where file watching and performance suffer. `.gitattributes` keeps scripts on LF. Run everything from the WSL shell, following the Ubuntu steps. GPU passthrough needs a current NVIDIA Windows driver, and it's a separate path to verify with `make smoke-gpu`, not an automatic guarantee. **None of this has been run yet.**
