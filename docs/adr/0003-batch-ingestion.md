# ADR-0003: Batched telemetry ingestion

**Status:** Accepted

## Context

A simulated fleet of up to thousands of batteries each generating telemetry
(PRD section 9's target scale is 10,000 devices at 1 event/second, i.e. 10,000
events/second). Contract section 16 and TDD constraint #3 ("use batching for
simulator-to-backend communication") both call for a batch path rather than one HTTP
request per reading.

## Decision

Provide two ingestion endpoints — `POST /api/v1/telemetry` for a single event and
`POST /api/v1/telemetry/batch` for many events in one request — with the simulator
always using the batch endpoint in practice (an `BatchAccumulator` collects readings and
flushes them as batches, per `simulator/delivery.py`). Both endpoints share the same
underlying idempotent-insert and current-state-UPSERT logic
(`app/services/telemetry_service.py`), so batching is purely a transport-level
optimization, not a second code path with different correctness guarantees.

## Alternatives considered

- **One HTTP request per telemetry reading.** Rejected: at fleet scale this means one
  full HTTP request (connection, validation, a single-row database round trip) per
  event, which doesn't amortize any of that fixed overhead across multiple readings.
- **A single combined endpoint that always expects an array, even for one event.**
  Rejected in favor of keeping the single-event endpoint too — Roadmap 1.7 explicitly
  treats single ingestion as "the first major project checkpoint" (the first time a
  reading travels the complete simulator → API → database path), and it remains useful
  for manual testing and the interactive API docs independent of the batch path the
  simulator actually uses.

## Consequences

Positive:

- One multi-row `INSERT ... ON CONFLICT DO NOTHING ... RETURNING` per batch replaces
  N single-row round trips, and the current-state UPSERT is similarly batched (reduced
  to one row per battery per request before the UPSERT statement is built).
- Phase 6's before/after benchmark showed the ingestion-path improvements (a
  transaction-shortening fix and an atomic-upsert fix, both downstream of processing
  data in batches) producing 2.0-2.5x throughput and 33-69% lower median latency —
  batching is what makes an optimization like "commit earlier to shorten the lock-hold
  window" meaningful in the first place, since a batch is exactly the unit of work whose
  transaction boundary can be tuned.
- Idempotency (Roadmap 1.8) had to work correctly under batching from day one — a
  duplicate `event_id` appearing twice in the *same* batch, or already stored from an
  earlier request, both had to be counted as duplicates rather than errors — which the
  chosen `ON CONFLICT DO NOTHING` approach handles identically either way.

Negative / accepted trade-offs:

- A batch is capped (`MAX_BATCH_SIZE`, default 1000) to bound request size and the work
  done in a single transaction — a batch over the limit is rejected with 413 rather than
  silently truncated, which the simulator's own batching logic has to respect.
- One bad `battery_id` anywhere in a batch fails the whole batch (checked up front, before
  any row is inserted) — simpler and safer than partial-batch semantics, at the cost of
  a single unregistered device being able to block an otherwise-valid batch it happens to
  share with others.
