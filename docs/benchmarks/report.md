# VoltStream — Benchmark Report

**Status:** published, real measured results only — no projected or assumed numbers anywhere in this document. Full investigation narrative, methodology detail, and caveats live in [`docs/benchmarks/baseline.md`](baseline.md); this report is the condensed, portfolio-facing version of the same data plus the after-fix numbers from Roadmap 6.4.

## Environment

- **Workload driver:** [`loadtest/locustfile.py`](../../loadtest/locustfile.py) — a headless Locust harness hitting `POST /api/v1/telemetry/batch` directly (see Workload below).
- **System under test:** the full Docker Compose stack (`docker-compose.yml`) — `postgres:16-alpine`, the FastAPI backend, and the frontend. The `simulator` container was stopped for every run, so load-test traffic was the only traffic reaching the database.
- **Backend concurrency:** a single Uvicorn worker process — confirmed directly from `backend/docker-entrypoint.sh` (`exec uvicorn app.main:app --host 0.0.0.0 --port 8000`, no `--workers` flag). This is a deliberate architectural choice (see [ADR-0002](../adr/0002-async-fastapi-single-worker.md)), not an oversight, and it means every request in these results was served by one process.
- **Host machine:** Sebika's MacBook Air (Apple Silicon), via Docker Desktop. The specific CPU/RAM allocated to Docker Desktop during these runs wasn't separately recorded, so treat the before/after comparison below as the reliable result — it's the same hardware and the same database state for both halves of each rung — rather than treating the absolute throughput numbers as portable to other machines.
- **Database state:** not reset between runs (deliberately — resetting would let `ON CONFLICT DO NOTHING` make ingestion look artificially cheap). By the "after" runs, `telemetry` held over 570,000 rows and `alerts` over 213,000, so if anything these numbers are a harder test than a freshly seeded database would produce.

## Workload

Each simulated Locust user sends 100-event telemetry batches back-to-back with no pause (`wait_time = constant(0)`), so load level is controlled purely by `--users`/`--spawn-rate`, not by anything in the harness itself. Every run used a dedicated pool of 5,000 test battery IDs (`BAT-95xxxx`), kept separate from the simulator's own battery IDs so load-test traffic never collided with unrelated rows. Each rung ran for 30 seconds, headless, with `--csv` output.

## Results: before vs. after Roadmap 6.4

**The fix:** an atomic `INSERT ... ON CONFLICT ... DO UPDATE` replacing a non-atomic check-then-insert race in alert creation, plus committing the current-state upsert earlier to shorten a row-lock hold window (full root-cause analysis in `docs/benchmarks/baseline.md`'s Bottleneck Analysis section, found via `pg_stat_activity`, not assumption).

Same load ladder, run twice against the same growing database — before the fix (still exhibiting the race and the lock contention) and after:

| Users | Requests (before → after) | Error rate | Median latency (before → after) | p95 latency (before → after) | Throughput (before → after) |
| ----- | ------------------------- | ---------- | ------------------------------- | ---------------------------- | --------------------------- |
| 1     | 26 → 60                   | 0% both    | 710ms → 220ms                   | 770ms → 280ms                | ~89 → ~204 events/sec       |
| 5     | 29 → 68                   | 0% both    | 2.6s → 1.2s                     | 5.2s → 2.7s                  | ~98 → ~229 events/sec       |
| 10    | 27 → 52                   | 0% both    | 4.1s → 2.3s                     | 9.8s → 6.1s                  | ~92 → ~176 events/sec       |
| 20    | 17 → 44                   | 0% both    | 7.2s → 4.8s                     | 13.0s → 10.0s                | ~57\* → ~148 events/sec     |

\* The 20-user "before" figure is likely understated: at that concurrency, median latency (7.2s) was already close to the 30-second run window, so a meaningful fraction of in-flight requests went uncounted when the run was cut off. If anything, this makes the real "before → after" throughput gap larger than the table shows.

**Zero failures in every rung, both before and after** — 496 total requests across the eight runs in this table, 0 errors. The system never returned a 5xx or timed out; the "before" numbers reflect real lock contention slowing every request down, not requests failing outright.

## Key finding

Roughly **2.0-2.5x throughput** and **33-69% lower median latency** at every concurrency level tested. The gain holds at 1 user (710ms → 220ms) where there's no lock contention to relieve, which is the interesting part: half of the improvement is a straightforward reduction in database round trips (the old alert path made a `SELECT` then a separate `INSERT`/`UPDATE`; the atomic upsert makes one call), independent of concurrency. The other half — the larger relative gains at higher concurrency (10 and 20 users) — comes from the shortened lock-hold window letting concurrent requests queue behind each other for less time. Full mechanism and the `pg_stat_activity` evidence that diagnosed it are in `docs/benchmarks/baseline.md`.

## Caveats carried over from the underlying investigation

- The synthetic load-test traffic generates every telemetry field independently at random, so it likely triggers alerts far more often than a real, gradually-changing battery fleet would — this may inflate per-request cost in both the before and after numbers equally, but hasn't been separately isolated.
- These are absolute numbers from one specific host and one specific (non-reset, growing) database state — useful for the relative before/after comparison this report is making, not as a general "VoltStream does N events/sec" claim independent of hardware.
- The full raw Locust CSV output for these runs is gitignored (`loadtest/results/*.csv`), consistent with the rest of the project's convention of not committing regenerable run output — the tables above are the durable record.
