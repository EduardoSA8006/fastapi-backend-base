# FastAPI Backend Base

[![CI](https://github.com/EduardoSA8006/fastapi-backend-base/actions/workflows/ci.yml/badge.svg)](https://github.com/EduardoSA8006/fastapi-backend-base/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[🇧🇷 Versão em Português](README.md)

A **FastAPI** backend template with fail-closed security by default, feature-first
architecture, and three levels of tests ready to go — designed to start new projects
with the foundation that usually takes weeks to harden.

> Names use the **`myapp`/`MyApp`** placeholder: when creating your project,
> run a global find/replace and you're done.

## What's included

- **Feature-first + core/shared architecture (semantic MVVM)** — documented feature
  contract (`router`/`service`/`repository`/`models`/`schemas`/`exceptions`/`tasks`),
  clear dependency rules (features never import from other features), and a lean
  composition root: all security policy lives in `core/security_guards.py`, testable
  in isolation.
- **Fail-closed production guards** — with `ENVIRONMENT=production`, boot is
  **refused** for: wildcard hosts, in-memory rate-limit, `DEBUG=true`, weak/default
  passwords in database/Redis/MinIO/Celery, predictable MinIO user, or Celery broker
  pointing to the same Redis instance as the rate-limit. `ENVIRONMENT` is required
  (no default — a typo in the variable name won't silently fall back to dev mode).
- **Complete error layer** — `AppException` hierarchy + global handler,
  `ErrorBoundary` ASGI middleware (standardized/opaque 500, never `text/plain`),
  structured JSON access log with `X-Request-ID` correlated all the way to crashes.
- **Secure Docker infrastructure by default** — Postgres, Redis (rate-limit),
  dedicated Celery Redis (destructive commands disabled), MinIO, and Celery
  worker/beat **with no published ports**; 3 segmented networks (worker has no route
  to the rate-limit Redis); API bound to loopback only; hardened containers
  (read-only, cap_drop ALL, resource limits).
- **Hardened Celery** — JSON-only serialization on all three surfaces,
  `acks_late`, prefetch 1, time limits; `core.ping` task as beat heartbeat; MinIO
  storage with async facade (`shared/storage.py`), lazy bucket creation, and typed
  errors.
- **Three test levels ready** (plus depth tooling):

| Level | Command | What it covers |
|---|---|---|
| Unit | `poetry run pytest` | 181 tests, 90% coverage gate, property-based (Hypothesis) on critical points |
| Integration | `poetry run pytest -m integration --no-cov` | Real Postgres/Redis/MinIO/broker (testcontainers), migrations, failure and recovery scenarios |
| E2E | `poetry run pytest -m e2e --no-cov` | Full compose stack via httpx, dev AND production mode, network isolation invariant |
| Mutation | `poetry run mutmut run` | Local assertion-strength metric (config ready) |

- **Complete CI** (GitHub Actions, SHA-pinned, Node 24): lint (ruff+bandit),
  mypy `--strict`, all 3 test levels, pip-audit, gitleaks, and trivy.

## How to use this template

1. **Use this template** on GitHub (or clone) and rename:

```bash
# inside the new project
grep -rl "myapp" --exclude-dir=.git . | xargs sed -i 's/myapp/yourproject/g; s/MyApp/YourProject/g'
```

2. Configure and run:

```bash
cp .env.example .env   # ENVIRONMENT is required — the example ships with development
poetry install
poetry run pytest      # 181 green before any line of yours
```

3. Bring up the full stack (API + Postgres + Redis ×2 + MinIO + Celery):

```bash
docker compose up -d --build
# API: http://127.0.0.1:8001 (Swagger at /docs; /api/v1/health and /api/v1/ready)
```

4. Create your first feature in `app/features/<name>/` following the contract
   (see "Project structure" below) — models go into `alembic/env.py`,
   tasks into the `include` list in `app/worker.py`.

## Requirements

- Python `>=3.12,<3.15` · [Poetry](https://python-poetry.org/) `>=2.0`
- Docker + Docker Compose (local stack, integration and e2e tests)

## Docker Services

`docker-compose.yml` starts 7 services segmented across 3 internal networks:

| Service | Image | Published port | Networks | Purpose |
|---|---|---|---|---|
| `api` | local build | `127.0.0.1:8001` | data, ratelimit, celery | FastAPI + Uvicorn |
| `worker` | local build | — | data, celery | Celery worker (concurrency 2) |
| `beat` | local build | — | data, celery | Celery beat — periodic scheduler |
| `db` | `postgres:16-alpine` | — | data | PostgreSQL (persistent data) |
| `redis` | `redis:7-alpine` | — | ratelimit | Rate-limit store (exclusive) |
| `redis-celery` | `redis:7-alpine` | — | celery | Celery broker + result backend |
| `minio` | `quay.io/minio/minio` | — | data | S3-compatible object storage |

**Internal networks** (least-privilege isolation):

| Network | Members | Purpose |
|---|---|---|
| `data_net` | api, worker, beat, db, minio | Database and storage access |
| `ratelimit_net` | api, redis | Rate-limit exclusive — worker/beat have no route to these keys |
| `celery_net` | api, worker, beat, redis-celery | Broker and result backend |

No service other than `api` exposes a port to the host. The `api` binds to loopback
(`127.0.0.1`) by default — adjust `API_BIND` consciously to expose on other interfaces.

---

The documentation below covers security and operational decisions in depth —
worth reading before your first deploy.

## Security

The application applies a set of global protections, configurable via environment
variables (see `.env.example`):

- **Rate limit** (slowapi): global per-IP limit (`RATE_LIMIT_DEFAULT`, default
  `100/minute`), stored in Redis in Docker (`RATE_LIMIT_STORAGE_URI`), with `429`
  responses including `Retry-After`. Can be disabled with `RATE_LIMIT_ENABLED=false`.
  Specific endpoints can have their own limits via `@limiter.limit(...)`.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`,
  `Permissions-Policy`, `Content-Security-Policy` (with `frame-ancestors 'none'`;
  exempt on `/docs`, `/redoc`, `/openapi.json`) and `Strict-Transport-Security`
  (when `HSTS_ENABLED=true`). Applied to all responses, including rejections
  (`400`/`413`/`429`).
- **CORS**: origins/methods/headers come from settings. The combination
  `CORS_ALLOW_CREDENTIALS=true` with `CORS_ALLOW_ORIGINS=["*"]` is blocked at
  startup as insecure.
- **Trusted hosts**: blocks disallowed `Host` headers (`400`). **In production,
  set `TRUSTED_HOSTS`** to real hosts — the default `["*"]` disables validation.
- **Body size limit**: rejects bodies above `MAX_BODY_SIZE` (`413`), counting real
  stream bytes (closes the bypass via `Transfer-Encoding: chunked`). Note: stream
  counting **takes effect when the body is consumed** by the handler; the case with
  a declared `Content-Length` is covered immediately by the fast-path (rejection
  before reading).
- **Observability**: each request receives an `X-Request-ID` (reused if sent by the
  client) and is logged with method, path, status, and IP; rejections (`4xx`) are
  logged as WARNING and errors (`5xx`) as ERROR.
- **Fingerprint**: the server starts with `--no-server-header` (does not emit
  `Server: uvicorn`).

### IP behind a proxy

By default the rate-limit uses the connection IP. Behind trusted reverse proxy(ies),
set `TRUST_PROXY=true` and `NUM_TRUSTED_PROXIES` to the number of trusted hops
(e.g. LB + nginx = `2`). The client IP is extracted by counting those hops from the
**right** of `X-Forwarded-For` (the entries the trusted proxies appended), preventing
spoofing.

> `TRUST_PROXY=true` is only safe if there are actually `NUM_TRUSTED_PROXIES` proxies
> rewriting the header upstream. Enabling it without that allows the client to control
> the value and bypass the rate-limit.

### Production (ENVIRONMENT=production)

Set `ENVIRONMENT=production`. In this mode the application **fails to start** if
there is an insecure configuration, forcing the operator to fix it:

- `TRUSTED_HOSTS` contains `"*"` → error (set real hosts).
- `RATE_LIMIT_STORAGE_URI` is `memory://` → error (use `redis://...`; with multiple
  workers/replicas, `memory://` makes the limit ineffective).
- `DEBUG=true` → error (prevents leaking stack traces).
- **Weak/default** (or absent) password in `DATABASE_URL` → error (use a strong password).
- **Weak/default** (or absent) password in `RATE_LIMIT_STORAGE_URI` Redis →
  error (parity with the database; use `redis://:PASSWORD@host:port/db`).

Warnings (don't fail boot, but require attention): `TRUST_PROXY=false` with a proxy
upstream (rate-limit collapses) and `TRUST_PROXY=true` without a real proxy (XFF
spoofable).

Additionally, `/docs`, `/redoc`, and `/openapi.json` are **disabled** in production
(do not expose the API surface). For multi-replica deployments, set
`RUN_MIGRATIONS_ON_START=false` and run `alembic upgrade head` as a separate deploy
step (prevents race conditions between containers).

> **Container healthcheck**: Docker's probe sends `Host: 127.0.0.1` by default,
> which `TrustedHostMiddleware` would reject under a restricted `TRUSTED_HOSTS`
> (→ 400 → restart loop). Set `HEALTHCHECK_HOST` to an allowed host (e.g.
> `HEALTHCHECK_HOST=api.myapp.com`). We intentionally don't loosen `TrustedHost`
> for the loopback — the probe carries the correct `Host` header.

### TLS

The application does not terminate TLS. For production deployments:

- Perform **TLS termination at the reverse proxy** (nginx/traefik/ALB) and redirect
  `HTTP → HTTPS`.
- With HTTPS active, set `HSTS_ENABLED=true`.
- For external/managed Postgres, require TLS on the connection:
  `DATABASE_URL=postgresql+psycopg://.../myapp?sslmode=require`.

### Defense in depth at the proxy/edge

The **body size** limit is authoritative within the application itself, regardless
of whether the handler consumes the body: with `Content-Length` there is a fast-path
via header; without it (`Transfer-Encoding: chunked`) the body is proactively drained
up to the limit before reaching the handler — closing the bypass where an endpoint
that doesn't read the body (e.g. `/health`) would never trigger the count.

> **Memory invariant**: the chunked path buffers the body in memory (up to
> `MAX_BODY_SIZE`) before re-delivering it to the handler. The peak is
> ~`MAX_BODY_SIZE` × concurrent chunked requests — an attacker can deliberately force
> this. Keep **`MAX_BODY_SIZE × --limit-concurrency` comfortably below the container's
> `mem_limit`** (defaults: 1 MB × 100 = ~100 MB < 512m), otherwise raising
> `MAX_BODY_SIZE` (e.g. for uploads) leads to **OOM kill**. For large uploads, prefer
> **streaming directly to storage**, not middleware buffering.

What remains **the responsibility of the edge/uvicorn** (out of scope for a body size
limit):

- **Slowloris / slow-POST** (body or headers sent byte by byte): **uvicorn does NOT
  cover this**. `--timeout-keep-alive` only closes **idle** keep-alive connections —
  a connection sending 1 byte every few seconds remains "active". With
  `--limit-concurrency 100`, ~100 slow connections can exhaust capacity without ever
  completing the body (thus never touching the rate-limit). Mitigation **requires a
  reverse proxy** with read timeouts: nginx `client_body_timeout` /
  `client_header_timeout` / `send_timeout` (or ALB/Cloudflare equivalents).
  **This is a production requirement.**
- **Direct app access (proxy bypass)**: do not publish the app port on all interfaces.
  With `TRUST_PROXY=true`, reaching the app directly (`:8001`) allows forging
  `X-Forwarded-For` and bypassing the per-IP rate-limit. The `docker-compose` binds
  to **loopback** by default (`API_BIND=127.0.0.1`); in production, prefer not
  publishing the port and only exposing the proxy (app on the internal network via
  the `api` hostname).
- **Defense in depth**: also maintain `client_max_body_size 1m;` (nginx) or
  equivalent, plus `--limit-concurrency` in uvicorn.
- **Rejections before rate-limit**: by design, cheap validations (invalid Host → 400,
  large body → 413) are **outside** the rate-limit (inner), to avoid spending a Redis
  operation on junk traffic — otherwise a flood of invalid requests would become a
  DoS against Redis itself. The trade-off is that this rejection path is not IP-limited;
  since each rejection is O(1) (no DB/Redis), the correct barrier is the
  **connection/rate limit at the edge/uvicorn** (`--limit-concurrency`), not the
  application rate-limit.

### Residual risks and security roadmap

- **Rate-limit by IP only**: mitigates simple bursts, but is bypassable/imprecise
  under **IP rotation / botnet** and penalizes users behind **shared NAT**
  (same IP). When authentication is added, add a **per-account** limit and
  **exponential backoff** on login failures (anti credential-stuffing).
- **Rate-limit fail-closed when Redis is down** (conscious choice): with the slowapi
  default (`swallow_errors=False`, no `in_memory_fallback`), an unavailable Redis
  makes rate-limited requests return **500** — the API doesn't go unprotected, but
  its **availability becomes coupled to Redis's**. Short socket timeouts
  (`socket_timeout`/`socket_connect_timeout=2s`, Redis only) prevent a slow Redis
  from hanging requests. Operational implications: **monitor/alert** Redis
  availability. The **probes (`/health` and `/ready`) are exempt from rate-limit**,
  so an offline Redis does **not** bring down liveness (no restart loop) — it's
  `/ready` that signals the degraded store.
- **`NUM_TRUSTED_PROXIES` misconfiguration / `TRUST_PROXY` misconfigured**: if the
  value is **larger** than the actual number of proxies (or `TRUST_PROXY=true`
  without a proxy rewriting the header), the extracted IP falls on a
  **client-controllable** entry → spoofing and rate-limit bypass. The app **cannot**
  detect network topology, so it doesn't fail boot; in production it emits an
  **explicit warning** for both `TRUST_PROXY=false` (rate-limit collapses behind
  proxy) and `TRUST_PROXY=true` (XFF spoofable if exposed directly).
- **`Cache-Control: no-store`** should be applied on sensitive endpoints when they
  exist (user data, tokens), preventing caching by intermediaries.
- **Log privacy (LGPD/GDPR)**: the access log records the client IP (personal data).
  Define **retention** and **legal basis** for those logs; use `LOG_CLIENT_IP=false`
  to stop logging the IP. The logged path **does not include query string**, but avoid
  **tokens in path** (e.g. `/reset/<token>`) — they would appear in the log; prefer
  tokens in the body/header.
- **Swagger UI via CDN (dev/staging only)**: when docs are enabled, Swagger loads
  assets from CDN (and the CSP is exempt on those paths) — if the CDN is compromised,
  there is XSS risk on the docs page. In **production the docs are disabled**, so
  there is no exposure; in dev/staging, consider serving assets locally or applying
  SRI if you want to close this.
- **Secrets via environment variables**: passwords reach the container through env
  (`DATABASE_URL`/`RATE_LIMIT_STORAGE_URI`), visible in `docker inspect` and
  `/proc/<pid>/environ`. Acceptable in the dev stack. In **production**, prefer
  Docker/Swarm secrets or a secrets manager mounting the password via **file** —
  `pydantic-settings` reads from `secrets_dir` (e.g. `/run/secrets`), avoiding
  exposing the credential in the process environment.
- **Base image pinned by tag, not by digest**: the `Dockerfile` uses
  `python:3.13-slim` (mutable tag). CI pins Actions by SHA; do the same with the
  Docker base — pin by `@sha256:<digest>` and update via Renovate/Dependabot.
- **`--forwarded-allow-ips` in uvicorn**: should remain at the **restricted default**
  (`127.0.0.1`). The source of truth for the client IP is the app's
  `resolve_client_ip` (`TRUST_PROXY`/`NUM_TRUSTED_PROXIES`). Enabling
  `--forwarded-allow-ips="*"` makes uvicorn rewrite `scope["client"]` with its own
  (more naive) XFF logic, creating a parallel and conflicting trust path.
- **Redis in plaintext (`redis://`)**: fine while Redis and API share the internal
  network of the same host. If Redis crosses a host boundary (managed, another node),
  switch to **`rediss://`** (TLS); the production password guard already covers
  `rediss://`.
- **slowapi private API**: the `429` handler uses `Limiter._inject_headers`
  (private method) to reproduce `Retry-After`/`X-RateLimit-*` headers.
  Protected by `try/except` (degrades to a clean `429`) and `slowapi` is pinned at
  `<0.2.0` — **re-evaluate on every version bump**.
- **CI/security**: the pipeline (`.github/workflows/ci.yml`) runs `ruff` (lint +
  SAST via `S`/bandit rules), `ruff format`, `mypy --strict`, `pytest`
  (coverage ≥90%), `pip-audit` (CVEs in deps), **gitleaks** (secret scanning) and
  **trivy** (Docker image CVEs) on every push/PR.

## Database migrations (Alembic)

Generate a new migration from models:

```bash
poetry run alembic revision --autogenerate -m "description of change"
```

Apply migrations:

```bash
poetry run alembic upgrade head
```

## Tests

```bash
poetry run pytest        # runs tests with coverage (90% minimum gate)
```

Coverage of `app/` is measured by `pytest-cov` and the build fails below 90%
(`--cov-fail-under=90`, configured in `pyproject.toml`).

## Code quality

```bash
poetry run ruff check .          # lint (strong rules: E,F,I,UP,B,S,SIM,PT,...)
poetry run ruff format .         # formatting
poetry run mypy .                # type checking (--strict)
poetry run pip-audit             # CVE audit of dependencies
```

Test levels and extra metrics (Docker required for integration/e2e):

```bash
poetry run pytest                          # unit (90% coverage gate)
poetry run pytest -m integration --no-cov  # real ephemeral infra (testcontainers)
poetry run pytest -m e2e --no-cov          # full compose stack + httpx
poetry run mutmut run && poetry run mutmut results  # mutation testing (LOCAL
                                           # metric, not a CI gate — see spec)
```

All these steps run in CI (`.github/workflows/ci.yml`) on every push/PR.

## Project structure

**Feature-first + core + shared** architecture (semantic MVVM):

```
app/
├── main.py                # Composition root: create_app() + production guards
├── worker.py              # Celery entrypoint (worker and beat)
├── core/                  # Cross-cutting infrastructure — zero business logic
│   ├── config.py          # Settings (pydantic-settings)
│   ├── database.py        # Engine, session and SQLAlchemy Base
│   ├── limiter.py         # Rate-limit (slowapi)
│   ├── logging.py         # Structured JSON logs
│   ├── client_ip.py       # Real IP resolution (X-Forwarded-For)
│   ├── security_guards.py # Weak credentials policy + Celery guards
│   └── middleware/        # ASGI middlewares (headers, body-size, observability)
├── shared/                # Cross-feature domain code
│   └── exceptions.py      # AppException hierarchy + global handler
└── features/              # One folder per feature (semantic MVVM)
    └── health/
        └── router.py      # Liveness/readiness probes
alembic/                   # Database migrations
tests/                     # Mirror the structure (core/, shared/, features/)
```

Feature anatomy: `router.py` (View — pure HTTP), `service.py`
(ViewModel — use case), `repository.py` + `models.py` (Model),
`schemas.py` (DTOs), `exceptions.py` (inherit from `shared.exceptions`),
`tasks.py` (Celery). Dependency rules: features → core/shared;
shared → core; a feature never imports from another feature.

## Environment variable reference

All variables read by `Settings` (via `pydantic-settings`). The **Default** column shows the value used when the variable is absent — except `ENVIRONMENT`, which is required.

### Application

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | — **required** | `development` \| `staging` \| `production` |
| `APP_NAME` | `MyApp Backend` | Name shown on `/` and in the docs |
| `DEBUG` | `false` | Debug mode — not allowed in production |
| `API_V1_PREFIX` | `/api/v1` | Prefix for all API routes |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./myapp.db` | Connection URL (SQLite in dev; Postgres in Docker) |

> In Docker, the compose injects `postgresql+psycopg://...@db:5432/myapp`.
> For external production, append `?sslmode=require`.

### Rate-limit

| Variable | Default | Description |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | Enables the global rate-limit |
| `RATE_LIMIT_DEFAULT` | `100/minute` | Global per-IP limit |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Limiter store — **use `redis://` in production** |

### CORS

| Variable | Default | Description |
|---|---|---|
| `CORS_ALLOW_ORIGINS` | `[]` | JSON list of allowed origins |
| `CORS_ALLOW_CREDENTIALS` | `false` | Allow cookies/auth — **never with `origins=["*"]`** |
| `CORS_ALLOW_METHODS` | `["*"]` | Allowed HTTP methods |
| `CORS_ALLOW_HEADERS` | `["*"]` | Allowed headers |

### Security and proxy

| Variable | Default | Description |
|---|---|---|
| `TRUSTED_HOSTS` | `["*"]` | Accepted `Host` header values — **set in production** |
| `MAX_BODY_SIZE` | `1048576` | Request body limit in bytes (1 MB) |
| `HSTS_ENABLED` | `false` | Emits `Strict-Transport-Security` — only with HTTPS |
| `TRUST_PROXY` | `false` | Extract real client IP from `X-Forwarded-For` |
| `NUM_TRUSTED_PROXIES` | `1` | Number of trusted proxies upstream |
| `LOG_CLIENT_IP` | `true` | Log client IP (personal data — GDPR/LGPD) |
| `READINESS_CACHE_SECONDS` | `3.0` | TTL of `/ready` cache in seconds |

### MinIO

| Variable | Default | Description |
|---|---|---|
| `MINIO_ENDPOINT` | `minio:9000` | MinIO `host:port` (no scheme) |
| `MINIO_USE_SSL` | `false` | TLS for MinIO connection |
| `MINIO_ROOT_USER` | `myapp` | Admin user — **non-obvious name required in production** |
| `MINIO_ROOT_PASSWORD` | `myapp-minio-dev` | Admin password — **strong password required in production** |
| `MINIO_BUCKET` | `myapp-files` | Default bucket (auto-created if absent) |

### Celery

| Variable | Default | Description |
|---|---|---|
| `CELERY_BROKER_URL` | `redis://:myapp@redis-celery:6379/0` | Broker URL |
| `CELERY_RESULT_BACKEND` | `redis://:myapp@redis-celery:6379/1` | Result backend URL |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | Synchronous in-process execution (tests only) |
| `CELERY_HEARTBEAT_SECONDS` | `60.0` | Beat heartbeat cadence |

### Docker Compose only (not read by `Settings`)

Variables used only by `docker-compose.yml` and `entrypoint.sh`:

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `myapp` | User created in the Postgres container |
| `POSTGRES_PASSWORD` | `myapp` | Postgres password |
| `POSTGRES_DB` | `myapp` | Database created in the container |
| `REDIS_PASSWORD` | `myapp` | Rate-limit Redis password |
| `CELERY_REDIS_PASSWORD` | `myapp` | Celery Redis password |
| `RUN_MIGRATIONS_ON_START` | `true` | Runs `alembic upgrade head` at container boot |
| `HEALTHCHECK_HOST` | `127.0.0.1` | `Host` header sent by the container healthcheck |
| `API_BIND` | `127.0.0.1` | API port bind interface |
| `API_PORT` | `8001` | Port published to the host |

---

## License

Distributed under the [MIT License](LICENSE).
