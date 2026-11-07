# Cloud GPU deployment

**The reference path: a Runpod GPU Pod running the worker image, an AWS EC2 host for web, API, identity and uploads checking, Amazon RDS for PostgreSQL, Amazon MQ for RabbitMQ 4.3, ElastiCache for Valkey, and Amazon S3.**

**Nothing here has been deployed.** Each step says how far it has really been tested:

- `provided`: the files exist and were checked statically.
- `locally validated`: it ran on a local machine.
- `deployed/tested`: it ran for real in the cloud. **No step has reached this.**

Provider facts were read on 2026-11-07. Check them again, because they change.

**Spending.** This creates billable resources. Don't start them, and don't leave a Pod running, without deciding that's what you want.

## Choices, and what was checked

| Need | Choice | Confirmed from the provider's documentation | Check before relying on it |
| --- | --- | --- | --- |
| Broker | [Amazon MQ for RabbitMQ 4.3](https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/rabbitmq-43.html), cluster deployment | Version 4.3 on `mq.m7g`. [Quorum queues](https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/quorum-queues-best-practices.html) are recommended with a cluster. Consumer timeout can be set by policy. Global QoS is rejected, and this worker uses per-consumer prefetch | Public access for a Runpod worker, and AMQPS on 5671. Step 6 proves both |
| Cache | [ElastiCache for Valkey](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Clusters.RBAC.html) | RBAC access strings, which need in-transit encryption | `noeviction` as a parameter, and Lua under RBAC. The API's rate limiter needs both. **Local tests use Redis 8, not Valkey** |
| Database | Amazon RDS for PostgreSQL | | Version 18 availability. Use separate users and databases for the app and identity |
| Storage | Amazon S3, private bucket | | POST policies with `content-length-range` are standard S3 |
| GPU | [Runpod Pod](https://docs.runpod.io/pods/storage) | See the storage table below | Current GPU availability where you need it |

CloudAMQP is a credible alternative: [its timeout is changeable in its console](https://www.cloudamqp.com/blog/quorum-queues-and-disk-space.html). Its RabbitMQ 4.3 availability wasn't checked.

### Runpod storage, as documented

| Storage | Survives a stop | Survives terminating | Use |
| --- | --- | --- | --- |
| Container disk | No | No | Attempt scratch, `/tmp/motionmirror-attempts` |
| Volume disk at `/workspace` | Yes | No | Don't rely on it for weights |
| Network volume at `/workspace` | Yes | Yes | **Weights**, `/workspace/models` |

A network volume must be attached when the Pod is created and can't be detached later. A Pod isn't a general VM, so this path doesn't use Docker inside it.

## Steps

### 1. Accounts and names: `provided`

You need an AWS account, a Runpod account, a container registry (GHCR through `release.yml`), a Hugging Face account for downloads, and two hostnames: `MM_APP_DOMAIN` for people and `MM_WORKER_DOMAIN` for workers. Generate every secret from a password manager or `openssl rand -base64 32`, and keep them in your secret store.

### 2. Sizing worksheet: `provided`

**A proposed profile, not a validated one.** Measure before trusting it.

| Item | Starting point | Measure with |
| --- | --- | --- |
| GPU memory | A 48 or 80 GB class, as a planning candidate | `make benchmark-gpu`, `peak_gpu_memory_bytes` in the manifest |
| Network volume | At least 90 GB. Weights take about 83 GB | `du -sh /workspace/models` |
| Container disk | At least 50 GB for scratch | `scratch_gb` in `benchmark.json`, as a first indication |
| CPU host | 4 vCPU, 8 GB, for API, web, identity and validation. ClamAV alone can use 3 GB | `docker stats` |
| Generation concurrency | 1. A GPU runs one generation at a time | |

### 3. Managed services: `provided`

**RDS.** Two databases with separate users: `motionmirror` and `keycloak`.

**S3.** A private bucket with Block Public Access, default encryption and versioning off. CORS allows POST and GET from `https://$MM_APP_DOMAIN`. Add a lifecycle rule to abort incomplete multipart uploads. Create an IAM user limited to this bucket.

**Amazon MQ.** A RabbitMQ 4.3 cluster, and a vhost `motionmirror`. Render and import the definitions, which create the queues, accounts with salted hashes, and the timeout policy. Amazon MQ's broker default is 30 minutes, so **the `long-gpu-attempts` policy is what lets a job run for hours**:

```bash
python3 scripts/render_definitions.py --env-file production.env --out definitions.json
curl --fail -u "$BROKER_ADMIN" -H 'content-type: application/json' \
  --data-binary @definitions.json "https://$MM_RABBIT_HOST/api/definitions"
```

Set `MM_RABBIT_CONSUMER_TIMEOUT_MS` to the same value (21600000 is 6 hours). The API can't read the broker's setting, so startup trusts this value and refuses one shorter than a job can run.

**ElastiCache.** A Valkey cache with in-transit encryption and RBAC, in the same VPC as the CPU host, so nothing else can reach it. Set `maxmemory-policy` to `noeviction`. Create the `mm-api` user with this access string, from `configs/redis/users.template.acl`:

```text
on ~mm:* &* -@all +@read +@write +@connection +@scripting +@transaction -keys -flushall -flushdb -config -shutdown
```

Then run `RedisBehaviourIT`'s scenarios by hand, or watch `mm_cache_total{result="error"}`, because the limiter's Lua hasn't been tested on Valkey.

### 4. CPU host: `provided`

An EC2 instance with Docker, and ports 80 and 443 open. Point both hostnames at it.

```bash
python3 scripts/render_realm.py --out /opt/motionmirror/realm-production.json    # No test accounts, no mock worker
export MM_REALM_FILE=/opt/motionmirror/realm-production.json
docker compose --env-file production.env -f infra/compose/compose.prod.yaml up -d
```

Every variable is required, and Compose refuses to start without it. `docs/SECURITY.md` shows how to verify image signatures first. The API refuses to start with mock mode, plain HTTP, insecure cookies, or plain connections to the broker or Redis (`locally validated` by `StartupChecksTest`). Migrations run at startup.

Then:

```bash
python3 scripts/deploy_smoke.py --app https://$MM_APP_DOMAIN --worker https://$MM_WORKER_DOMAIN
```

Each check should say `ok`. It proves the site answers, and that the worker API, metrics and identity administration aren't public (`locally validated` by `tests/tools/test_deploy_smoke.py`, against a fake server only).

### 5. Worker image: `provided`

From a Mac, build for the GPU host, or let `release.yml` do it:

```bash
make build-worker-amd64 IMAGE=ghcr.io/<owner>/<repo>/worker-cuda:v0.1.0
docker push ghcr.io/<owner>/<repo>/worker-cuda:v0.1.0
docker buildx imagetools inspect ghcr.io/<owner>/<repo>/worker-cuda:v0.1.0   # Must show linux/amd64
```

Weights are never in the image.

### 6. Start the Pod: `provided`

Create a network volume in a data center with the GPU you want, then a Pod using your image by digest, with the network volume at `/workspace`. Fill in the environment from `infra/runpod/worker.env.template`, using Pod secrets. Don't expose any port. The worker needs none.

Override the start command to run these checks first, in this order:

```bash
python -m motionmirror.cli.gpu_check       # Before spending time on downloads
python -m motionmirror.cli.worker_check    # Identity, broker access and limits, and signed URLs. Loads no model
MM_MODEL_CACHE=/workspace/models HF_HUB_OFFLINE=0 python -m motionmirror.cli.models --profile wan
```

`worker_check` is what proves public broker access and AMQPS from the Pod (`locally validated` against the local stack, on the host). Then set the start command back to the image's entrypoint. The worker registers and consumes from its queues, and acts only under leases.

### 7. Real output: `pending GPU validation`

1. A short real preview, through the app. Check the output.
2. `python -m motionmirror.cli.spike_infer` for a measured benchmark, then keep the `benchmark.json`.
3. The 30-second acceptance run. Check it against the checklist in `docs/TESTING.md`.

### 8. Monitoring: `provided`

`infra/observability` works as a starting point, and `make observability-up` runs it locally (`locally validated`). In the cloud, send traces to your chosen backend with `MM_TRACING_ENABLED` and `MM_OTLP_TRACES_ENDPOINT`, and scrape `/actuator/prometheus` from inside the network, with `MM_METRICS_TOKEN` as a bearer token. Verify that telemetry really arrives. GPU memory comes from the manifests and `nvidia-smi`.

### 9. Recovery: `locally validated`

`make test-failure-recovery` exercises a killed worker, an API restart, a frozen worker and a storage outage on the local stack. See `docs/runbooks/OPERATIONS.md` for backup, restoring with tombstones, and draining. Repeat a worker kill and an adapter rollback on the real deployment before trusting it.

### 10. Cost controls: `provided`

Concurrency stays at one per GPU. Clip length is limited by `MM_MAX_SOURCE_SECONDS`, attempt time by `MM_MAX_ATTEMPT_DURATION`, queue length by each queue's `x-max-length`, and per-user activity by quotas. Uploads are checked on the CPU host, so **the Pod can be stopped between sessions**, and jobs wait safely in the queue until it returns. Turn on budget alerts with both providers. There's no automatic idle shutdown, so stop the Pod yourself.

### Cost worksheet

```text
Estimated job cost = GPU hourly rate x billed GPU seconds / 3600
                   + attributable CPU, storage, request and egress costs
```

Billed seconds include model loading and idle allocation. Take `seconds` from `benchmark.json`, and today's rates from each provider's pricing page, with the date you read them. No price is quoted here because none has been verified. Transfers between Runpod and S3 cross clouds, so check egress charges and allow for slow large transfers.

### Teardown

| Action | What stops billing | What remains billable |
| --- | --- | --- |
| Stop the Pod | GPU time | The network volume, and possibly a stopped Pod's volume disk |
| Terminate the Pod | The Pod and its volume disk, whose data is deleted | The network volume |
| Delete the network volume | Its storage, and the weights must be downloaded again | Nothing on Runpod |
| Stop the EC2 instance | Compute | Its EBS volume and any Elastic IP |
| Delete the Amazon MQ, ElastiCache and RDS resources | Those services | RDS snapshots you keep, and S3 until you empty and delete the bucket |

**Never assume stopping compute stops storage charges.** Check each provider's billing page afterwards.

## Sources

Read on 2026-11-07: [Amazon MQ RabbitMQ 4.3](https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/rabbitmq-43.html), [Amazon MQ configurable values](https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/configurable-values.html), [quorum queue practices](https://docs.aws.amazon.com/amazon-mq/latest/developer-guide/quorum-queues-best-practices.html), [ElastiCache RBAC](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Clusters.RBAC.html), [ElastiCache security](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/SecurityPillar.html), [Runpod Pod storage](https://docs.runpod.io/pods/storage), [Runpod storage types](https://docs.runpod.io/pods/storage/types.md).
