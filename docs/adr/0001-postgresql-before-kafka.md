# ADR-0001: PostgreSQL before Kafka

**Status:** Accepted

## Context

VoltStream ingests continuous telemetry from a simulated battery fleet and needs to
serve current state, history, analytics, alerts and predictions from it. A message
broker (Kafka) is one common way to build a telemetry pipeline at scale, decoupling
ingestion from downstream processing and enabling replay and multiple independent
consumers.

The Technical Design Document (section 33 / 38) set an explicit constraint up front:
"Do not add Kafka, Redis, or specialized infrastructure without a defined reason," and
named the concrete conditions that would justify it — database throughput can't keep up
with producers, durable buffering is needed, event replay is useful, or multiple
independent consumers need the same stream.

## Decision

Start with PostgreSQL as the single system of record, written to directly from the
FastAPI backend's request handlers (batch UPSERT/INSERT), with no message broker in
front of it. Revisit only if load testing produces evidence that one of the TDD's
trigger conditions has actually been met.

## Alternatives considered

- **Kafka (or another broker) from the start.** Rejected as premature: it adds an
  operational component (a cluster, topic/partition design, consumer group management)
  to defend against a bottleneck that hadn't been measured yet. The TDD's own risk table
  lists "scope becomes too large" as a named risk, with "keep Kafka, Redis, Kubernetes
  outside MVP" as its mitigation.
- **PostgreSQL with a message queue for buffering only** (e.g. as a shock absorber ahead
  of the database, without full event-sourcing). Also rejected for the same reason —
  no measured evidence yet that PostgreSQL itself was the constraint.

## Consequences

Positive:

- Phase 6's load testing (`docs/benchmarks/baseline.md`) found the actual first-order
  bottleneck was *not* database throughput at all — it was application-level lock
  contention: a non-atomic check-then-insert race in alert creation, and an ingestion
  transaction holding `battery_current_state` row locks longer than necessary. Both were
  fixed in application code (an atomic `INSERT ... ON CONFLICT ... DO UPDATE`, and
  committing earlier to shorten the lock-hold window), producing a measured 2.0-2.5x
  throughput improvement and 33-69% lower median latency at every concurrency level
  tested — with zero new infrastructure. This is direct evidence the decision was
  right for this project's actual workload: the code, not PostgreSQL, was the limiting
  factor.
- One fewer moving part to run, monitor, and reason about in Docker Compose and in this
  portfolio's own operational story.

Negative / accepted trade-offs:

- No built-in replay, no independent consumer groups, no durable buffering ahead of the
  database. If a future requirement genuinely needs one of those (see
  [ADR-0007](0007-kafka-deferred.md)), introducing Kafka later is still available — this
  decision is revisited on evidence, not treated as permanent.
