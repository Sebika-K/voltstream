# VoltStream

A production-inspired real-time telemetry and predictive monitoring platform for a
simulated fleet of smart batteries - built end to end, phase by phase, with every
architecture and performance claim backed by a reproducible measurement rather than
assumption.

## Overview

VoltStream simulates a fleet of battery devices (residential, solar, commercial, and
deliberately faulty profiles), ingests their telemetry through a FastAPI backend into
PostgreSQL, and turns that into something operationally useful: a live-updating fleet
dashboard, automatic anomaly and offline alerts, depletion-time predictions, and the
observability to see how the system itself is behaving under load. It's built the way a
small production service would be - idempotent ingestion, containerized deployment,
retry and backpressure under failure, structured logging, and load-tested, evidence-
driven performance work — not just a demo that only works on the happy path.

**Current status:** the full MVP is complete and verified, the system has
been hardened for real failure, measured and optimized under load,
and instrumented with application metrics. See [`docs/adr/`](docs/adr/) and the phase-by-phase status docs referenced throughout this document for the full history, including every bug found and fixed along the way.

## Architecture

```text
Battery Fleet Simulator  --batch HTTP/JSON-->  FastAPI Backend  -->  PostgreSQL
  (simulator/, asyncio,        POST /api/v1/        (backend/, 1 Uvicorn      (batteries,
   retry + bounded queue)      telemetry[/batch]      worker, async)           telemetry,
                                                           |                    battery_
                                                           |-- Telemetry &      current_state,
                                                           |   anomaly rules    alerts)
                                                           |-- Prediction
                                                           |   (baseline + ML)
                                                           |-- 3 background loops
                                                           |   (offline detection,
                                                           |    SSE publisher,
                                                           |    metrics refresh)
                                                           |
                                                     GET /metrics (Prometheus format)
                                                           |
                                                     GET /api/v1/stream (SSE)
                                                           |
                                                           v
                                                  React Dashboard (frontend/)
```

This is deliberately a modular monolith, not microservices or an event-streaming
pipeline — Kafka and Redis were considered and explicitly **not** adopted, per measured
evidence rather than a default architectural preference. The full as-built diagram, data
model, and the reasoning behind every major structural decision live in:

- [`docs/architecture/README.md`](docs/architecture/README.md) — the complete system
  diagram, data model, real-time delivery design, and observability surface.
- [`docs/adr/`](docs/adr/) — seven Architecture Decision Records (PostgreSQL before
  Kafka, async FastAPI/single worker, batch ingestion, the current-state table, SSE over
  WebSockets, the ML baseline strategy, and why Kafka is deferred), each with context,
  the decision, alternatives considered, and consequences grounded in this project's own
  measured results.

## Features

- **Realistic fleet simulation** — configurable device count, four usage profiles
  (residential / solar / commercial / faulty), controllable fault injection, all running
  concurrently via `asyncio`.
- **Idempotent telemetry ingestion**, single-event and batch, safe to retry without
  creating duplicate history.
- **Live fleet dashboard** that updates itself via Server-Sent Events — no polling —
  aggregated to about once per second regardless of underlying telemetry rate.
- **Battery list and detail views** with historical charts, current state, and
  depletion prediction.
- **Fleet-wide analytics** (online/offline counts, average state of charge, available
  energy, charging/discharging/idle breakdown, active/critical alert counts).
- **Automatic anomaly detection**, five alert types (low state of charge, high
  temperature, rapid discharge, voltage anomaly, device offline), deduplicated into a
  single ongoing incident per battery per type rather than one alert per reading.
- **Depletion-time prediction** — a physics/rate baseline with automatic fallback if no
  trained model is loaded or able to answer a given request.
- **Containerized, one-command startup** (`docker compose up --build -d`) for all four
  services, in dependency order via health checks.
- **Resilience under real failure** — simulator retry with exponential backoff and
  jitter, a bounded send queue so a slow backend can't cause unlimited memory growth,
  and backend fixes for two real deadlocks and a controlled 503 found by deliberately
  testing failure rather than assuming it away.
- **Structured JSON logging** with request-ID tracing across the simulator and backend.
- **A repeatable Locust load-test harness**, a documented baseline benchmark, and at
  least one optimization with a reproducible before/after measurement.
- **Prometheus-format application metrics** (`GET /metrics`) — telemetry throughput,
  errors by code, ingestion/database latency, active batteries, active alerts, and
  predictions served.

## Tech Stack

| Layer         | Technology                                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------------------------- |
| Backend       | Python, FastAPI, Pydantic / pydantic-settings, SQLAlchemy 2.x (async) + asyncpg, Alembic                      |
| Simulator     | Python, `asyncio`, `httpx`                                                                                    |
| Frontend      | React, TypeScript, Vite                                                                                       |
| ML            | pandas, NumPy, scikit-learn, joblib                                                                           |
| Database      | PostgreSQL 16                                                                                                 |
| Observability | `prometheus-client` (Prometheus text-exposition format)                                                       |
| Load testing  | Locust                                                                                                        |
| Testing       | pytest, pytest-asyncio, httpx (backend and simulator both test against a real PostgreSQL instance, not mocks) |
| Deployment    | Docker, Docker Compose                                                                                        |

## Quick Start (Docker Compose)

From a clean machine with Docker installed:

```bash
cp .env.example .env
# edit .env and set a real POSTGRES_PASSWORD

docker compose up --build -d
```

This builds and starts four services, in dependency order (each waits for the previous
one's health check to pass):

| Service     | What it is                                                                     | Where            |
| ----------- | ------------------------------------------------------------------------------ | ---------------- |
| `postgres`  | PostgreSQL 16 with a persistent named volume                                   | `localhost:5432` |
| `backend`   | FastAPI API. Applies database migrations automatically on startup              | `localhost:8000` |
| `simulator` | Simulated battery fleet (default 100 devices) sending telemetry to the backend | (no port)        |
| `frontend`  | React dashboard served by nginx                                                | `localhost:3000` |

Then open the dashboard at **http://localhost:3000**.

Useful commands:

```bash
docker compose ps                       # status and health of every service
docker compose logs -f simulator        # watch telemetry being sent (or: backend, frontend, postgres)
curl http://localhost:8000/ready        # {"status":"ready","database":"connected"}
curl http://localhost:8000/metrics      # Prometheus-format application metrics
open http://localhost:8000/docs         # interactive API docs

docker compose down                     # stop everything, keep the database data
docker compose down -v                  # stop everything AND delete the database volume
```

After changing code, `docker compose up --build -d` rebuilds and restarts what changed
(images are built with `COPY . .`, not live-mounted).

Note: the frontend image bakes in the address the browser uses to reach the backend
(`http://localhost:8000` by default, from `BACKEND_PORT`), so if you publish the backend
on a different host or port, rebuild the frontend and add the frontend's address to
`CORS_ORIGINS`.

### Running the backend locally without Docker

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example .env
# edit backend/.env: set DATABASE_URL to point at a Postgres instance you control

alembic upgrade head
uvicorn app.main:app --reload
```

## Demo

The fastest way to see it live, once `docker compose up --build -d` is running:

1. Open **http://localhost:3000** — the fleet dashboard updates itself within seconds as
   the simulator's 100 devices report in, no refresh needed.
2. Click into any battery for its detail page — live state, historical chart, and a
   depletion-time prediction if it's currently discharging.
3. Set `FAULT_RATE` above `0.0` in `.env` and restart the simulator
   (`docker compose up -d simulator`) to see anomaly alerts appear on the `/alerts` page
   in real time as faulty devices report out-of-range readings.
4. `docker compose stop simulator` and watch the Offline count climb on the dashboard as
   devices stop reporting — then `docker compose start simulator` and watch them recover.

A recorded walkthrough will be linked here once it exists.

## Performance Results

Full methodology and the complete baseline ladder are in
[`docs/benchmarks/baseline.md`](docs/benchmarks/baseline.md) — this section summarizes
the headline result: a documented before/after optimization (Roadmap 6.4), closing a
real race condition found under load.

**The bottleneck**, found from evidence (`pg_stat_activity`), not assumption: a
non-atomic check-then-insert race in alert creation, plus an ingestion transaction
holding row locks longer than necessary. **The fix:** a single atomic
`INSERT ... ON CONFLICT ... DO UPDATE`, and committing earlier to shorten the lock-hold
window. Same Locust load ladder run before and after, 1/5/10/20 concurrent users, 30s
each:

| Users | Median (before → after) | Throughput (before → after) |
| ----- | ----------------------- | --------------------------- |
| 1     | 710ms → 220ms           | ~89 → ~204 events/sec       |
| 5     | 2.6s → 1.2s             | ~98 → ~229 events/sec       |
| 10    | 4.1s → 2.3s             | ~92 → ~176 events/sec       |
| 20    | 7.2s → 4.8s             | ~57 → ~148 events/sec       |

Roughly **2.0-2.5x throughput** and **33-69% lower median latency** at every
concurrency level tested — verified by a new concurrency test, the full backend suite,
and a live load test reproducing the original failure with 0 errors afterward.

## ML Evaluation

Real, measured comparison on a genuinely wide dataset (1,131,812 rows of
accumulated telemetry), evaluated with actual MAE/RMSE rather than assumed:

| Model                         | MAE (min) | RMSE (min)                                                 |
| ----------------------------- | --------- | ---------------------------------------------------------- |
| **Physics baseline**          | **44.14** | **53.14**                                                  |
| Random Forest                 | 45.64     | 52.00                                                      |
| HistGradientBoostingRegressor | 56.64     | 65.13                                                      |
| Linear Regression             | 834.68    | 872.05 (fails by extrapolating outside its training range) |

**The physics baseline wins.** This is why VoltStream serves every live prediction with
the baseline today rather than a trained model — the serving/fallback machinery was
built for real, it just resolves to the baseline every time because that's
what the numbers say to do, per the project's own rule that model choice follows
evidence, not assumed complexity. See
[ADR-0006](docs/adr/0006-ml-baseline-before-advanced-models.md) for the full reasoning,
including an earlier baseline evaluation on a smaller dataset that produced a misleadingly
good number and was caught by the same measure-before-trusting discipline.

## Testing

- **Backend:** 223 tests passing (pytest + pytest-asyncio), run against a real,
  reachable PostgreSQL instance — no mocked database. Covers business logic (anomaly
  rules, prediction formulas), the full REST API surface, idempotency, concurrency
  (deliberately overlapping requests), structured logging, and the Prometheus metrics
  introduced in Phase 7.
- **Simulator:** 132 tests passing, covering battery state evolution, usage profiles, retry/backoff behavior, and the bounded send queue.
- **ML pipeline** (`ml/`): a dedicated test suite covering feature engineering, dataset
  building, baseline evaluation, model training, and model persistence.
- **Live failure testing**, not just unit tests: the database and backend were
  deliberately stopped for 20s and 90s to verify the simulator survives, telemetry
  resumes with no duplicates, and the backend recovers cleanly — this is how two real
  deadlocks and a missing 503 were actually found (Phase 5).
- **Load testing**: a repeatable Locust harness (`loadtest/`) against
  `POST /api/v1/telemetry/batch`, used for both the baseline benchmark and the Phase 6.4
  before/after comparison above.

## Engineering Decisions

The full reasoning for each major architectural choice — with alternatives considered
and consequences grounded in this project's own measured results — is in
[`docs/adr/`](docs/adr/):

| Decision                                                                                  | One-line rationale                                                                                                                                     |
| ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [PostgreSQL before Kafka](docs/adr/0001-postgresql-before-kafka.md)                       | No infrastructure added without a measured, specific reason — and Phase 6 proved the real bottleneck was in application code, not database throughput. |
| [Async FastAPI, single worker](docs/adr/0002-async-fastapi-single-worker.md)              | I/O-bound workload; one worker keeps the in-process SSE broadcaster and background loops coherent without extra coordination infrastructure.           |
| [Batched ingestion](docs/adr/0003-batch-ingestion.md)                                     | Amortizes fixed per-request overhead across many readings; what makes lock-hold-window optimizations possible at all.                                  |
| [Current-state table](docs/adr/0004-current-state-table.md)                               | O(1) "what's happening now" instead of scanning growing history on every read.                                                                         |
| [SSE over WebSockets](docs/adr/0005-sse-instead-of-websockets.md)                         | The dashboard only needs server-to-client updates; SSE is the simpler fit and reuses plain HTTP.                                                       |
| [ML baseline before advanced models](docs/adr/0006-ml-baseline-before-advanced-models.md) | Measured evidence over assumed complexity — and the baseline turned out to genuinely win.                                                              |
| [Kafka deferred](docs/adr/0007-kafka-deferred.md)                                         | Not rejected permanently — deferred until one of its named trigger conditions is actually observed with evidence.                                      |

## Lessons Learned

Real problems found and fixed during this project, kept here because they're more
instructive than a clean success story would be:

- **A partial unique index's `WHERE` predicate has to match `ON CONFLICT`'s
  `index_where` structurally, not just semantically.** `resolved = false` and
  `resolved IS false` mean the same thing to Postgres in a query, but only the exact
  same text matches for conflict-target inference — the mismatch doesn't fail at import
  time, it fails at request time with a confusing "no unique or exclusion constraint"
  error.
- **Lock two rows in the same order everywhere, or risk a deadlock.** Two concurrent
  requests touching the same battery rows in different orders is exactly how Postgres
  deadlocks; sorting by ID before writing (twice — once for ingestion, once for offline
  detection) fixed two separate real deadlocks found by deliberately testing backend
  outages rather than assuming retry logic alone was enough.
- **A sandbox that can only `py_compile` code proves it's syntactically valid, not
  correct.** Every real bug in this project (the deadlocks, the `ON CONFLICT` predicate
  mismatch, the original alert-creation race) was only caught by running the actual test
  suite against a real database — a lesson that shaped how this project split work
  between an assistant that writes code and a human who runs it for real.
- **pytest and a live Docker Compose stack sharing one database will eventually collide.**
  Running the test suite while the simulator and backend are still live against the same
  Postgres instance produces exactly the symptoms you'd expect — unexpected leftover
  rows, and a bulk `DELETE` deadlocking against a concurrent writer. Not a bug in either
  side; just two real writers that need to not run against the same data at the same
  time.
- **Measure before trusting a "good" number, especially in ML.** An early baseline
  evaluation on a small dataset looked excellent (MAE 0.23 minutes) — and was wrong,
  an artifact of too little data diversity, not real accuracy. Only re-evaluating on a
  much larger, more varied dataset (1.1M+ rows) revealed the honest result.
- **The cheapest fix is sometimes "commit sooner," not "add infrastructure."** The
  single biggest performance win in this project (2-2.5x throughput) came from an
  atomic upsert and a shorter transaction — no caching layer, no message broker, no
  extra workers. Load-testing first, and optimizing only what the evidence pointed at,
  is what made that possible instead of guessing.

## Configuration

All configuration comes from environment variables; see [`.env.example`](.env.example)
for the full list with explanations. Nothing is hard-coded, and no secrets are committed
to source control — `.env` is gitignored.

| Variable                                                                                    | Used by                 | Purpose                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`                                       | docker-compose, backend | PostgreSQL credentials/database name                                                                                                                                     |
| `POSTGRES_PORT`                                                                             | docker-compose          | Host port PostgreSQL is published on                                                                                                                                     |
| `DATABASE_URL`                                                                              | backend, Alembic        | Full async SQLAlchemy database URL                                                                                                                                       |
| `BACKEND_PORT`                                                                              | docker-compose          | Host port the backend is published on                                                                                                                                    |
| `ENVIRONMENT`                                                                               | backend                 | Free-form environment label (`development`, etc.)                                                                                                                        |
| `LOG_LEVEL`                                                                                 | backend, simulator      | Logging verbosity                                                                                                                                                        |
| `LOG_FORMAT`                                                                                | backend, simulator      | `json` (default) or `text`                                                                                                                                               |
| `FRONTEND_PORT`                                                                             | docker-compose          | Host port the dashboard is published on (default 3000)                                                                                                                   |
| `CORS_ORIGINS`                                                                              | backend                 | Comma-separated browser origins allowed to call the API                                                                                                                  |
| `DEVICE_COUNT` / `TELEMETRY_INTERVAL_SECONDS` / `FAULT_RATE` / `RANDOM_SEED` / `BATCH_SIZE` | simulator               | Fleet size and behavior                                                                                                                                                  |
| `RETRY_MAX_ATTEMPTS` / `RETRY_BASE_DELAY_SECONDS` / `RETRY_MAX_DELAY_SECONDS`               | simulator               | Retry with exponential backoff and jitter when the backend is unavailable (defaults 10 / 0.5s / 30s)                                                                     |
| `QUEUE_MAX_BATCHES` / `SEND_WORKERS`                                                        | simulator               | Bounded send queue and number of senders (defaults 20 / 2) — when the queue is full the simulated batteries wait, so a slow backend cannot cause unlimited memory growth |

## Logging

Both the backend and the simulator write **structured logs**: one JSON object per line
on standard output, with the same core fields so one tool can read both.

```json
{
  "timestamp": "2026-09-21T16:43:59.266Z",
  "level": "INFO",
  "service": "backend",
  "event": "telemetry_batch_processed",
  "request_id": "1fe4bed2...",
  "received": 100,
  "inserted": 100,
  "duplicates": 0,
  "battery_count": 99,
  "duration_ms": 165.41
}
```

Every backend response carries an `X-Request-ID` header, and the simulator sends its own
per batch, so a single ID follows a batch across both services:

```bash
docker compose logs simulator backend | grep <request_id>
```

Logs never contain secrets (no connection strings or passwords are logged).

## API

All product APIs live under `/api/v1` (see `http://localhost:8000/docs` for the full,
interactive spec once the backend is running). Operational endpoints are bare and
unversioned:

| Method | Path       | Purpose                                                         |
| ------ | ---------- | --------------------------------------------------------------- |
| GET    | `/health`  | Liveness. Always `200`, independent of PostgreSQL.              |
| GET    | `/ready`   | Readiness. `200` when PostgreSQL is reachable, `503` otherwise. |
| GET    | `/metrics` | Prometheus-format application metrics.                          |

## Database migrations

Alembic is the sole schema-management mechanism — the application never calls
`Base.metadata.create_all()`.

```bash
cd backend
alembic revision --autogenerate -m "add some_table"
# review the generated migration, then:
alembic upgrade head
```

## What's next

Recorded demo, published benchmark report, ML report.
Prometheus/Grafana and Kafka remain deliberately deferred — both optional, revisited only if there's a measured reason to.
