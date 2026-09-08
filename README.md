# VoltStream

A production-inspired real-time telemetry and predictive monitoring platform for a
simulated fleet of smart batteries. See the project documents (`docs/` and the attached
Product/Technical/Implementation/Roadmap specs) for the full system design.

**Current status: project foundation only.** This repository currently implements the
Phase 0 foundation from the development roadmap: repository scaffolding, a FastAPI
application with liveness/readiness endpoints, and a PostgreSQL database configured
through Docker Compose with async SQLAlchemy + Alembic wired up. Battery registration,
telemetry ingestion, the simulator, the frontend, real-time delivery, alerts, and ML
prediction are **not** implemented yet -- they follow in later phases.

---

## Repository structure

```text
voltstream/
├── backend/            FastAPI application (Python, async SQLAlchemy, Alembic)
├── simulator/           Battery fleet simulator (not yet implemented)
├── frontend/             React dashboard (not yet implemented)
├── ml/                    Depletion-prediction model training/serving (not yet implemented)
├── load-tests/           Load-testing harness (not yet implemented)
├── infrastructure/       Deployment/infra configuration (not yet implemented)
├── docs/                  Project documentation
├── docker-compose.yml   Local orchestration (postgres + backend)
└── .env.example          Documented environment configuration
```

## Tech stack (foundation)

- Python, FastAPI, Pydantic / pydantic-settings
- SQLAlchemy 2.x (async) + asyncpg
- Alembic for schema migrations
- PostgreSQL 16
- Docker / Docker Compose

## Prerequisites

- Docker and Docker Compose (recommended path), **or**
- Python 3.11+ and a locally running PostgreSQL instance, for running the backend
  directly on the host.

## Quick start (Docker Compose)

```bash
cp .env.example .env
# edit .env and set a real POSTGRES_PASSWORD

docker compose up --build
```

This starts PostgreSQL (with a persistent named volume and a health check) and the
FastAPI backend, which waits for PostgreSQL to report healthy before starting.

Once running:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/ready
# {"status":"ready","database":"connected"}

# Interactive API docs
open http://localhost:8000/docs
```

Stop everything with `docker compose down` (add `-v` to also remove the Postgres data
volume).

## Running the backend locally without Docker

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp ../.env.example .env
# edit backend/.env: set DATABASE_URL to point at a Postgres instance you control,
# e.g. postgresql+asyncpg://voltstream:change-me@localhost:5432/voltstream

# apply migrations (currently a no-op: no application tables exist yet, but this
# confirms Alembic is correctly configured against your database)
alembic upgrade head

uvicorn app.main:app --reload
```

## Running tests

Tests require a real, reachable PostgreSQL database (the readiness tests exercise an
actual connection) -- either the Docker Compose `postgres` service or a local instance,
configured via `DATABASE_URL`.

```bash
cd backend
source .venv/bin/activate   # if using a virtualenv
export DATABASE_URL=postgresql+asyncpg://voltstream:change-me@localhost:5432/voltstream

pytest
```

## Configuration

All configuration comes from environment variables; see [`.env.example`](.env.example)
for the full list with explanations. Nothing is hard-coded, and no secrets are committed
to source control -- `.env` is gitignored.

| Variable | Used by | Purpose |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | docker-compose, backend | PostgreSQL credentials/database name |
| `POSTGRES_PORT` | docker-compose | Host port PostgreSQL is published on |
| `DATABASE_URL` | backend, Alembic | Full async SQLAlchemy database URL |
| `BACKEND_PORT` | docker-compose | Host port the backend is published on |
| `ENVIRONMENT` | backend | Free-form environment label (`development`, etc.) |
| `LOG_LEVEL` | backend | Logging verbosity |

## API (implemented so far)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. Always returns `200 {"status": "ok"}` -- independent of PostgreSQL. |
| GET | `/ready` | Readiness. Returns `200 {"status": "ready", "database": "connected"}` when PostgreSQL is reachable, `503 {"status": "not_ready", "database": "unavailable"}` otherwise. |

All future product APIs (batteries, telemetry, fleet summary, alerts, predictions,
streaming) will live under `/api/v1`, per the Technical Design Document.

## Database migrations

Alembic is the sole schema-management mechanism -- the application never calls
`Base.metadata.create_all()`. No application tables exist yet, so there are currently no
migration files to apply; `alembic upgrade head` is a no-op today and will start doing
real work once the first models (`batteries`, `telemetry`, ...) are added in Phase 1.

Standard workflow once models exist:

```bash
cd backend
alembic revision --autogenerate -m "add batteries table"
# review the generated migration, then:
alembic upgrade head
```

## Design notes / deviations from the project documents

- **`GET /health` and `GET /ready` are unversioned**, matching the Implementation
  Contract (section 33) and TDD (section 11), which both list them as bare paths
  distinct from the versioned `/api/v1` product surface.
- **`backend/app/{repositories,schemas,services,ml,models}/` are not yet created.** The
  TDD's suggested layout includes these as part of the layered architecture, but the
  task brief also asks not to create placeholder files merely to populate directories.
  Since Task 1 introduces no business logic, persistence, or ML code, these packages
  are deferred to the phase that first needs them (Phase 1 for `models`/`repositories`,
  per the roadmap) rather than committed as empty scaffolding now.
- **`docker-compose.yml` currently defines only `postgres` and `backend`.** The TDD's
  full Docker architecture (section 26) also lists `simulator` and `frontend` services,
  but those components have no implementation yet in this repository, so compose
  services for them would reference nonexistent Dockerfiles. They will be added in the
  phases that implement those components.
- **A readiness timeout (`DB_READY_TIMEOUT_SECONDS`, default 2s) was added** so a hung
  database connection can't hang `/ready` indefinitely. This isn't specified explicitly
  in the contract but follows directly from "readiness checks required downstream
  connectivity" without contradicting any stated behavior.

## What's next

Per the roadmap, Phase 1 (Telemetry Pipeline) is next: the battery model and profiles,
the async fleet simulator, the telemetry Pydantic contract, the `batteries` and
`telemetry` SQLAlchemy models with their first Alembic migration, and the
registration/ingestion endpoints. Not started in this repository yet.
