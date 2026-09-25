# ADR-0007: Kafka introduction deferred

**Status:** Accepted (revisit if conditions change)

## Context

Contract section 33/64 and TDD section 33 define Kafka as explicitly *not* part of the
initial architecture, but sketch a possible future evolution (`Simulator → Ingestion API
→ Kafka → Storage/Alerts/ML consumers → PostgreSQL`, topic `battery.telemetry.v1`, keyed
by `battery_id` for per-battery ordering) and name the conditions that would justify
actually building it: database throughput can't keep up with producers, durable
buffering is needed, event replay would be useful, multiple independent consumers need
the same stream, or ingestion needs to be decoupled from persistence for its own sake.
Phase 8 of the Roadmap makes this evaluation an explicit, optional, evidence-gated task
rather than a default next step.

## Decision

Do not implement Kafka. Phase 8 (Event-Driven Evolution) remains deferred, revisited
only if one of the conditions above is actually observed with evidence — not
implemented as a default "next logical step" or to demonstrate familiarity with the
technology on its own.

## Alternatives considered

- **Build the Kafka prototype anyway, as a portfolio/learning exercise**, independent of
  whether the project needs it. Considered and rejected for this iteration: the Roadmap
  is explicit that "Kafka should not be considered an improvement unless the results
  justify that conclusion," and building it without a real driving problem would produce
  exactly the kind of unjustified-infrastructure complexity ADR-0001 and the TDD's own
  risk table (`keep Kafka, Redis, Kubernetes outside MVP`) were written to avoid.
- **Partially adopt Kafka for just the alerts or ML consumer**, to decouple those from
  the main ingestion path without a full pipeline rewrite. Also deferred — no evidence
  yet that ingestion and alert/ML processing being coupled is causing a real problem;
  Phase 6 found and fixed the actual bottleneck (ADR-0001) without touching this
  coupling at all.

## Consequences

Positive:

- Phase 6's load testing directly tested whether the database was the constraint (one
  of the Roadmap's named trigger conditions) and found it wasn't — the fix was an atomic
  upsert and a shorter lock-hold window in application code, not more ingestion
  throughput capacity. This is concrete evidence *against* needing Kafka right now, not
  just an absence of evidence for it.
- The system stays operationally simple: one Docker Compose stack, no broker cluster,
  no consumer-group rebalancing to reason about, no second place events can get lost or
  duplicated relative to PostgreSQL.

Negative / accepted trade-offs:

- If a future requirement genuinely needs durable replay, multiple independent
  consumers of the same telemetry stream, or ingestion decoupled from a database that
  really can't keep up, that work hasn't been done and would start from the TDD's
  sketch (`battery.telemetry.v1`, keyed by `battery_id`) rather than from an existing
  prototype.
- This ADR is deliberately not "permanently rejected" — its own title says deferred, and
  the trigger conditions it's waiting on are written down precisely so a future decision
  to build it (or not) can point to actual evidence either way, the same discipline
  ADR-0001 and ADR-0006 already followed.
