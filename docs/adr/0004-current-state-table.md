# ADR-0004: Separate current-state table

**Status:** Accepted

## Context

Most product surfaces — the fleet dashboard, the battery list, alert rules, offline
detection, predictions — need to answer "what is this battery doing *right now*," not
"what has it ever reported." `telemetry` is an append-only history table that grows
without bound; answering "right now" from it means finding the newest row per battery
every time, which gets slower as history accumulates. TDD constraint #4 states this
directly: "separate historical telemetry from current fleet state."

## Decision

Maintain `battery_current_state`, one row per battery, always overwritten (never
appended to) by an atomic UPSERT run as part of every successful ingestion
(`app/services/telemetry_service.py::_upsert_current_state`). `telemetry` remains the
sole historical record; `battery_current_state` is an explicitly disposable, always-fast
mirror of the newest known reading.

## Alternatives considered

- **Reconstruct current state from `telemetry` on every read** (e.g.
  `SELECT ... ORDER BY timestamp DESC LIMIT 1` per battery, or a window function over the
  whole table). Rejected as the steady-state approach: this is exactly the "Current-State
  Query" experiment the Roadmap's Phase 6 (6.4) names as a candidate optimization to
  compare against the table-based approach, and the table wins by construction —
  it turns an O(history size) lookup into an O(1) row read.
- **A cache (Redis) in front of `telemetry` for current state.** Rejected per ADR-0001's
  broader "no extra infrastructure without a measured reason" stance — TDD section 34
  explicitly lists this as a possible *future* Redis use case, not a starting
  requirement, since PostgreSQL current-state reads already satisfy latency needs.

## Consequences

Positive:

- Every read path that needs "now" (fleet summary, battery detail, offline detection,
  the alert rules, prediction) reads one indexed row instead of scanning or sorting
  history, independent of how much telemetry has accumulated.
- The UPSERT's own `WHERE battery_current_state.last_seen <= excluded.last_seen` guard
  means an out-of-order/late-arriving event can never move current state backwards in
  time — an invariant that would be much harder to guarantee against ad hoc
  reconstruction queries written at each call site.
- A batch touching the same battery twice reduces to one row before the UPSERT
  statement is built (the newest timestamp wins), and every writer locks battery rows in
  the same sorted order — the fix for a real deadlock found during Phase 5's failure
  testing, made possible because this is one well-defined write path rather than several.

Negative / accepted trade-offs:

- Current state is a second thing that can (very briefly) disagree with history if a
  process crashes between the `telemetry` insert and the `battery_current_state` UPSERT
  — mitigated by both happening inside the same transaction/commit boundary for single
  ingestion, and by 6.4's trade-off (documented in `claude/phase-6-performance-
  engineering-status.md`) of committing telemetry + current-state before evaluating
  alert rules, accepting that alert evaluation for one batch could rarely be left
  incomplete rather than ever risking losing real device data.
- One more table to keep the schema and migrations for, and one more piece of business
  logic (the UPSERT) that has to be correct — accepted because "current fleet state"
  is read far more often than telemetry history is reconstructed from scratch.
