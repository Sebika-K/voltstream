# VoltStream — Architecture

This is the *as-built* architecture, current through Phase 7 (Observability). The
Technical Design Document's own diagram (section 39) describes the intended baseline
before implementation started; this document describes what actually exists in the
repository today, and links to the ADRs that explain why each major decision was made.

## System diagram

```text
                         ┌─────────────────────────┐
                         │   Battery Fleet          │
                         │   (simulator/, asyncio)  │
                         │   bounded send queue +   │
                         │   retry w/ backoff+jitter│
                         └────────────┬─────────────┘
                                      │
                            Batch HTTP/JSON
                    POST /api/v1/telemetry[/batch]
                                      │
                                      ▼
                    ┌───────────────────────────────────┐
                    │        FastAPI Backend             │
                    │        (backend/, 1 Uvicorn worker)│
                    │                                     │
                    │  app/api/            HTTP routes    │
                    │  app/services/                      │
                    │   ├─ telemetry_service.py            │
                    │   │   (validate, idempotent insert,  │
                    │   │    current-state UPSERT)         │
                    │   ├─ anomaly_detection_service.py    │
                    │   │   (5 rule types, create/retain/  │
                    │   │    resolve, atomic upsert)       │
                    │   ├─ prediction_service.py            │
                    │   │   (physics baseline, ML fallback) │
                    │   ├─ fleet_service.py / battery_service.py │
                    │   └─ three background loops:          │
                    │       offline_detector.py (5s)         │
                    │       realtime_publisher.py (1s, SSE)  │
                    │       metrics_refresher.py (10s)       │
                    │                                        │
                    │  GET /metrics  (Prometheus text format)│
                    └───────────────────┬────────────────────┘
                                         │
                         ┌───────────────┼────────────────┐
                         ▼               ▼                ▼
                  ┌────────────┐  ┌─────────────┐  ┌──────────────┐
                  │ PostgreSQL │  │ In-process  │  │ ML artifact  │
                  │            │  │ Broadcaster │  │ (joblib, if  │
                  │ batteries  │  │ (asyncio    │  │  one beats   │
                  │ telemetry  │  │  queues per │  │  the         │
                  │ battery_   │  │  SSE client)│  │  baseline —  │
                  │  current_  │  └──────┬──────┘  │  currently   │
                  │  state     │         │         │  none does)  │
                  │ alerts     │         │ SSE      └──────────────┘
                  └────────────┘         │ GET /api/v1/stream
                                         ▼
                                ┌─────────────────┐
                                │  React Dashboard │
                                │  (frontend/, TS  │
                                │   + Vite)        │
                                └─────────────────┘
```

All four application services (`postgres`, `backend`, `simulator`, `frontend`) run under
one `docker-compose.yml`, started in dependency order via health checks
(`claude/phase-5-reliability-containerization-status.md` has the full detail).

## What changed from the TDD's original diagram

The TDD's section-39 diagram was the *starting* design. Two things turned out different
once real measurements came in, both recorded as ADRs:

- **No Kafka, no Redis, no second consumer.** The TDD listed specific conditions under
  which Kafka would be justified (database can't keep up, durable buffering, replay,
  independent consumers). Phase 6's load testing found the real bottleneck was an
  *application-level* one — a non-atomic check-then-insert race in alert creation, plus
  an ingestion transaction holding row locks longer than it needed to — not a database
  throughput ceiling. Fixing both in code produced a 2-2.5x throughput improvement with
  no new infrastructure. See [ADR-0001](../adr/0001-postgresql-before-kafka.md) and
  [ADR-0007](../adr/0007-kafka-deferred.md).
- **A third background loop, for metrics.** Phase 7 added `metrics_refresher.py`
  alongside the two loops the TDD anticipated (offline detection, realtime publishing) —
  same shape (an independent `asyncio` task started from the app's lifespan), refreshing
  the two gauge-shaped metrics (active batteries, active alerts) on its own schedule
  rather than computing them on the request path, for the same reason the ingestion path
  was already being protected from extra work in Phase 6.

## Data model (PostgreSQL)

| Table | Purpose |
|---|---|
| `batteries` | Device metadata — capacity, wiring limits, profile type. Set once at registration. |
| `telemetry` | Append-only historical readings. One row per accepted event, keyed by a client-supplied `event_id` (idempotency). |
| `battery_current_state` | One row per battery, always overwritten — "what is this battery doing right now" without scanning history. See [ADR-0004](../adr/0004-current-state-table.md). |
| `alerts` | One row per alert incident. A partial unique index enforces "at most one unresolved alert per battery + type" at the database level, not just in application code. |

## Real-time delivery

The dashboard never polls. `GET /api/v1/stream` is a Server-Sent Events connection; an
in-process `Broadcaster` (one Python process, one Uvicorn worker) fans a once-per-second
aggregated `fleet_update` out to every connected client, with a bounded per-client queue
so a slow browser tab can't grow server memory without limit. See
[ADR-0005](../adr/0005-sse-instead-of-websockets.md).

## Prediction

Every prediction request tries a loaded ML model first and falls back to a physics
baseline if none is loaded or the model can't answer. In this project's actual measured
result, the baseline is the real winner on the full dataset, so the fallback path is what
serves every live prediction today. See
[ADR-0006](../adr/0006-ml-baseline-before-advanced-models.md).

## Observability

`GET /metrics` exposes Prometheus-format counters, histograms and gauges for telemetry
throughput, errors, ingestion/database latency, active batteries, active alerts, and
predictions served (Phase 7.1). Prometheus and Grafana themselves (7.2/7.3) are not
deployed — they're optional per the Roadmap and deferred until after Phase 9.

## Further reading

- [Architecture Decision Records](../adr/) — the seven required ADRs, each with context,
  decision, alternatives considered, and consequences.
- [`docs/benchmarks/baseline.md`](../benchmarks/baseline.md) — the load-test methodology
  and all measured performance numbers behind the claims made here and in the ADRs.
- `claude/phase-*-status.md` in the project's Claude docs — the detailed build log for
  every phase, including bugs found and fixed along the way.
