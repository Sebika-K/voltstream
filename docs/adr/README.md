# Architecture Decision Records

The seven ADRs the Technical Design Document (section 36) requires, each recording
context, decision, alternatives considered, and consequences. Numbered in the order the
underlying decisions were actually made during the project, not the order the TDD lists
them.

| # | Decision | Status |
|---|---|---|
| [0001](0001-postgresql-before-kafka.md) | PostgreSQL before Kafka | Accepted |
| [0002](0002-async-fastapi-single-worker.md) | Async FastAPI, single Uvicorn worker | Accepted |
| [0003](0003-batch-ingestion.md) | Batched telemetry ingestion | Accepted |
| [0004](0004-current-state-table.md) | Separate current-state table | Accepted |
| [0005](0005-sse-instead-of-websockets.md) | SSE instead of WebSockets | Accepted |
| [0006](0006-ml-baseline-before-advanced-models.md) | Simple ML baseline before advanced models | Accepted |
| [0007](0007-kafka-deferred.md) | Kafka introduction deferred | Accepted (revisit if conditions change) |
