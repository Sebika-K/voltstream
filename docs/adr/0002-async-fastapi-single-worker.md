# ADR-0002: Async FastAPI, single Uvicorn worker

**Status:** Accepted

## Context

The backend needs to handle many concurrent I/O-bound operations at once: HTTP
requests, PostgreSQL queries, and — once Phase 3 added real-time delivery — long-lived
SSE connections held open per browser tab, plus multiple independent background loops
(offline detection, the realtime publisher, and later the metrics refresher). The
Implementation Contract (section 63) fixed an explicit starting constraint: V1 begins
with exactly one Uvicorn worker, because "this keeps application-managed background
tasks and in-process SSE broadcasting coherent," and multiple workers require shared
realtime/background coordination to be addressed first.

## Decision

Build the backend as an async FastAPI application (`async def` route handlers,
SQLAlchemy 2.x's async API over `asyncpg`), running as a single Uvicorn worker process.
Background work (offline detection, the SSE fleet-update publisher, the metrics
refresher) runs as `asyncio.Task`s started from FastAPI's `lifespan` handler, living in
the same process and event loop as request handling — not as separate worker processes
or a task queue.

## Alternatives considered

- **Sync Flask/Django with a thread or process pool.** Rejected: I/O-bound work (mostly
  waiting on the database) is a better fit for cooperative async than for
  thread-per-request, and it avoids the complexity of coordinating background loops
  across worker processes.
- **Multiple Uvicorn workers from the start.** Rejected per the Contract's explicit
  constraint above — the in-process `Broadcaster` (ADR-0005) and every background loop
  assume they're the only instance running; multiple workers would mean multiple
  independent broadcasters (a browser tab could miss updates published to a sibling
  worker) and multiple independent offline-detection/metrics-refresh loops doing
  redundant work. Worker count is explicitly left open to benchmark later, once shared
  coordination (e.g. via Postgres LISTEN/NOTIFY or Redis) is designed for it.

## Consequences

Positive:

- A telemetry batch request, a live SSE stream, and three background loops all coexist
  cheaply in one process without needing inter-process coordination — the in-process
  `Broadcaster` in particular (ADR-0005) is only possible because of this choice.
- Phase 6's atomic-upsert fix and transaction-shortening fix (ADR-0001) were entirely
  about database round trips and lock-hold time, not CPU contention — consistent with
  this being an I/O-bound workload where async concurrency, not more processes, is the
  right lever.

Negative / accepted trade-offs:

- A single worker means CPU-bound work (if any crept into a request handler) would block
  the whole process. This is why the TDD's constraint #5 ("keep CPU-heavy training
  outside request handlers") matters — ML training runs offline via `ml/`, never inside
  a request.
- A single worker is a ceiling on request-handling parallelism that will eventually
  need addressing if throughput requirements grow past what one process can serve;
  the Contract explicitly defers that design rather than solving it prematurely.
