# Troubleshooting

## GPU and models

| Problem | Cause | Fix |
| --- | --- | --- |
| `CUDA_OOM` | Settings too large for this GPU | Lower size or segment length. Try `MM_WAN_OFFLOAD=sequential`, which is slower and uses less GPU memory |
| `gpu_check` fails on driver | NVIDIA Container Toolkit or passthrough | Check `nvidia-smi` on the host, then inside a container |
| `gpu_check` fails on onnxruntime | The GPU provider can't load | Pose would otherwise crawl on the CPU, so the worker refuses (`UNSUPPORTED_CAPABILITY`). Check the CUDA libraries |
| `MODEL_MISSING` | Weights not installed | The message names the command |
| `IntegrityError` during a download | A partial or changed file | Run it again, it resumes. If it repeats, report it |
| Attention or dtype errors | Runtime mismatch | Compare with `docs/DEPENDENCY_MATRIX.md`. Use the image rather than an ad hoc install |

## Apple Silicon

| Problem | Cause | Fix |
| --- | --- | --- |
| `Cannot convert a MPS Tensor to float64` | MPS has no float64 | Use float32 or lower |
| An operator isn't implemented on MPS | Unsupported op | Set `PYTORCH_ENABLE_MPS_FALLBACK=1` only for that case, and record it. SAM 2 tracking doesn't need it |
| The machine slows to a crawl | Memory pressure | Stop other apps and the stack. `--watchdog` stops a benchmark at 6 GB of swap growth |
| `exec format error`, or a very slow container | arm64 and amd64 mismatch | `make doctor` reports emulation. Build the GPU image with `make build-worker-amd64` |

## Sign-in

| Problem | Cause | Fix |
| --- | --- | --- |
| Redirect loop or `invalid_redirect_uri` | `MM_PUBLIC_ORIGIN` doesn't match the address in the browser | Use exactly that address |
| Issuer mismatch | The token's issuer differs from `MM_OIDC_PUBLIC_ISSUER` | Both must be the public URL. The check stays on |
| A new client isn't found locally | Keycloak imports a realm only once | `make destroy-all-local-data`, then `make up-mock`. This removes local data |

## Broker

| Problem | Cause | Fix |
| --- | --- | --- |
| A long job restarts | The broker's consumer timeout is too short | Import the definitions, whose policy sets it, and keep `MM_RABBIT_CONSUMER_TIMEOUT_MS` in step |
| Messages in `motionmirror.dead.*` | Delivery limit reached, or a malformed task | Inspect them first. The reconciler usually recovers the job anyway |
| `ACCESS_REFUSED` in a worker | Wrong account for that queue | `make worker-check` shows which |
| Global QoS channel error | RabbitMQ 4.3 removed it | Workers use per-consumer prefetch. Check for a custom client |

## Redis

| Problem | Cause | Fix |
| --- | --- | --- |
| `NOPERM` | The ACL or RBAC user is missing a permission | Compare with `configs/redis/users.template.acl` |
| `OOM command not allowed` | `noeviction` is doing its job | Raise memory. Nothing is silently evicted |
| New work refused, heartbeats fine | The limiter fails safe while Redis is down | Restore Redis. Running jobs carry on |

## Storage

| Problem | Cause | Fix |
| --- | --- | --- |
| A browser upload fails with CORS | The bucket's CORS rule | Allow POST and GET from the app's origin |
| `SignatureDoesNotMatch`, or a worker's download fails | The URL was signed for another address | Set the right endpoint: public for browsers, worker for workers. Run `make worker-check` |
| `STORAGE_ERROR` | Storage is down, or a URL expired | It retries with fresh URLs |

## Results

| Problem | What to try |
| --- | --- |
| The mask drifts onto someone else | Add a mark on that frame. The preview tints the tracked person so this shows up |
| `TARGET_LOST` | The dancer is hidden too long. Trim, or add marks where they come back |
| `MULTIPLE_SHOTS` | Trim to one continuous shot |
| Spins look wrong or the face flickers | A model limit. Check at a higher preset, and compare seeds |
| Another dancer changed | They were inside the edit rectangle. See `docs/MODEL_CUSTOMIZATION.md` |
| Audio drifts | Check the output's duration against the selection in its manifest, and report it. Tests hold this to one frame |
| A job seems stuck | Open its events. Stages are real and there are no invented percentages. If heartbeats stopped, the lease expires and it retries |

## Training and adapters

| Problem | Fix |
| --- | --- |
| Licence gate refusal | The dataset isn't reviewed for this purpose. See `docs/DATASETS.md` |
| `outside parties` refusal | Get the dataset authors' permission before using a rented GPU, then confirm it |
| `train-lora` says below tested hardware | The trainer's authors say one 80 GB GPU can't train this model. Decide before spending |
| Training out of memory | Expected on small hardware. See `docs/TRAINING.md` |
| `BASE_REVISION_MISMATCH` | The adapter was registered against another base model |
| `HASH_MISMATCH` | The stored file differs from the run manifest. Upload again |
| `RESEARCH_ADAPTER` | Research-only data can't reach production. That's deliberate |
| `ADAPTER_INCOMPATIBLE` | Not every layer attached. Check the base model it was trained on |
