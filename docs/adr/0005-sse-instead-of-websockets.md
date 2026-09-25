# ADR-0005: SSE instead of WebSockets

**Status:** Accepted

## Context

The fleet dashboard needs to update itself without the browser manually polling
(Roadmap 3.1). Real-time delivery to a browser is most commonly done with either
WebSockets (bidirectional) or Server-Sent Events / SSE (server-to-client only, plain
HTTP). Contract sections 34-36 specify the shape this had to take: one endpoint
carrying `{"event_type", "timestamp", "payload"}` messages, REST staying authoritative
(SSE carries only incremental updates after an initial REST snapshot), updates
aggregated to roughly once per second, and bounded memory even against a slow or
stalled client.

## Decision

Use Server-Sent Events: `GET /api/v1/stream` is a single long-lived HTTP response the
server keeps writing small `fleet_update` messages into. An in-process `Broadcaster`
(`app/services/broadcaster.py`) holds one bounded `asyncio.Queue` per connected client;
a background loop (`realtime_publisher.py`) wakes once a second, and only if at least
one client is connected, recomputes the fleet summary and publishes one aggregated
event to every subscriber.

## Alternatives considered

- **WebSockets.** Rejected for V1: the dashboard only ever needs server→browser updates
  (no client-to-server real-time messages), and WebSockets would add bidirectional
  connection management and a different reconnect/backpressure story for a capability
  this project doesn't use. SSE is plain HTTP — it reuses ordinary HTTP infrastructure,
  reconnects automatically via the browser's native `EventSource`, and is a strictly
  simpler fit for a server-to-client-only requirement.
- **Redis pub/sub (or another broker) behind the broadcaster**, to support multiple
  backend processes sharing one stream of updates. Rejected per ADR-0002: V1 runs a
  single Uvicorn worker specifically so an in-process broadcaster is sufficient — no
  Redis/Kafka/Postgres LISTEN-NOTIFY is needed yet. This would become necessary if/when
  multiple workers are introduced.

## Consequences

Positive:

- Simple to implement and test end to end — Phase 3's own live verification was a plain
  `curl` against the SSE stream while telemetry flowed, no separate protocol tooling
  needed.
- The Contract's aggregation requirement (updates collapsed to ~1/second regardless of
  how much telemetry actually arrived) maps naturally onto "one background loop, one
  tick per second" — there's no risk of accidentally publishing per-event, since
  publishing only happens from the timer, never from the ingestion path itself.
- Backpressure is handled per-client: each subscriber's queue is bounded (`maxsize=32`,
  roughly 30 seconds of buffering); a slow client's oldest queued event is dropped to
  make room for the newest rather than growing server memory without bound — an
  acceptable trade because the Contract already requires a REST refetch on reconnect to
  restore correctness after any gap.

Negative / accepted trade-offs:

- SSE is one-directional — any future feature needing the browser to push real-time data
  back to the server would need a different mechanism alongside it (not a reason to
  regret this choice for the current requirement, since no such feature exists yet).
- The in-process `Broadcaster` is explicitly tied to ADR-0002's single-worker
  constraint; scaling to multiple backend processes means revisiting this design, not
  just adding more workers.
