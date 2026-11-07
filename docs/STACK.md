# Stack

Exact versions as locked on 2026-11-07. `docs/DEPENDENCY_MATRIX.md` covers models and hardware tiers.

| Layer | Tool | Version | Purpose | Licence note |
| --- | --- | --- | --- | --- |
| API | Java, Spring Boot | 21, 4.1.2 | System of record, security, job control | Apache-2.0 |
| Web | Node, Next.js, React | 26.13, 16.4.5, 19.3.1 | UI | MIT |
| Web | TypeScript, Tailwind | 5.9.3, 4.3.5 | Types, styling | Apache-2.0, MIT |
| Worker | Python, uv | 3.12, 0.13.7 | Media and models | PSF, MIT |
| Database | PostgreSQL | 18 | Jobs, outbox, sessions | PostgreSQL licence |
| Broker | RabbitMQ | 4.3, quorum queues | Dispatch | MPL-2.0 |
| Cache | Redis | 8 | Presets, capabilities, rate limits | See upstream, used unmodified |
| Identity | Keycloak | 26.8.2 | OIDC | Apache-2.0 |
| Storage | SeaweedFS | 4.52 | Local S3. Replaces archived MinIO | Apache-2.0 |
| Proxy | Caddy | 2 | Routing, certificates | Apache-2.0 |
| Observability | Prometheus, Grafana, Tempo | 3.16.0, 13.3.0, 2.10.10 | Optional `full` profile | Apache-2.0, AGPL-3.0, AGPL-3.0, run unmodified as separate services |
| Security | ClamAV | 1.5.4 | Optional `security` profile | GPL-2.0, run as a separate service |
| Scanners | Gitleaks, Semgrep, Trivy | 8.30.1, 1.184.0, 0.76.2 | `make scan` | MIT, LGPL-2.1, Apache-2.0 |
| Load | k6 | 1.8.2 | `make test-load` | AGPL-3.0, a test tool only |

## Compose profiles

| Profile | Adds | Command |
| --- | --- | --- |
| `lite` | The full application with the mock worker | `make up-mock` |
| `full` | Prometheus, Grafana, Tempo | `make observability-up` |
| `gpu` | The real worker, on a CUDA machine | `make up-gpu` |
| `security` | ClamAV | `MM_CLAMD_HOST=clamav docker compose --env-file .env -f infra/compose/compose.yaml --profile lite --profile security up -d` |

`make doctor` reports the machine and fails on silent emulation. Host ports use the 55xxx range to avoid native services.
