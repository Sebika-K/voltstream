# VoltStream — Phase 6.2 Baseline Benchmark

**Roadmap item:** 6.2 Baseline Benchmark (Phase 6 — Performance Engineering)
**Date:** 2026-09-23 to 2026-09-24
**Harness:** `loadtest/locustfile.py` (built and verified repeatable in 6.1), run with Locust in headless mode against a full local Docker Compose stack (backend + postgres + frontend; the `simulator` container was stopped for every run so load-test traffic was the only thing hitting the database).

## Purpose

6.1 built a repeatable Locust harness against `POST /api/v1/telemetry/batch`. This document is 6.2's job: run that harness at increasing load levels and write down what actually happens, without trying to fix anything yet. The roadmap is explicit about this — "do not optimize before capturing baseline" — because benchmarking a half-fixed system would make this baseline useless for a later before/after comparison in 6.4. Root-cause analysis is 6.3's job, not this document's; this document only reports what was measured.

## Methodology

- Each simulated Locust user sends 100-event batches back-to-back with no pause (`wait_time = constant(0)`), so load level is controlled entirely by `--users` / `--spawn-rate`, not by anything in the harness itself.
- Every run used a dedicated pool of 5,000 test batteries (`BAT-95xxxx`), separate from the simulator's own battery IDs, so that two unrelated traffic sources were never competing for the same `battery_current_state` rows. A pool this large also makes it very unlikely for the harness's own concurrent requests to collide with each other on the same battery.
- Runs 1-5 below all use `--headless` with a fixed `--run-time`, so each run stops itself automatically. This was adopted partway through the investigation after a manually-timed run accidentally ran for 19 minutes instead of the intended 30 seconds — which itself became a useful lesson: test duration is a variable that has to be held constant for runs to be comparable, the same as concurrency or pool size.

## Known caveats (read before drawing conclusions from the table below)

1. **The database was never reset between runs.** Every batch inserts real rows with fresh, unique event IDs by design (reusing IDs would let `ON CONFLICT DO NOTHING` make ingestion look artificially cheap, which isn't representative of real traffic). By the time of Rung 3 below, `telemetry` held 570,900 rows and `alerts` held 213,137 — essentially none of which existed when the very first trace of this system was taken back in Phase 5.2 (a single uncontested batch measured ~178ms round trip then). The numbers below reflect a progressively more loaded database, not a freshly seeded one. That's flagged here so nobody reads these numbers later as "VoltStream's steady-state performance" without the caveat.
2. **The synthetic telemetry likely over-triggers alerts.** The harness generates every field independently at random for every event, so state of charge, status, etc. have no continuity from one reading to the next. Roughly one in three events sent during this investigation has produced an alert row — far more than a real, gradually-changing battery fleet would generate. This may be inflating the per-request cost in a way that isn't representative of production traffic, and is worth checking directly in 6.3.
3. **A harness bug limited pool sizes to under 1,000 battery IDs until it was fixed on 2026-09-23/24.** `BATTERY_ID_PREFIX` originally left only 3 digits of head-room (`BAT-950` + `{n:03d}`), which broke as soon as the pool crossed 1,000 batteries (`BAT-9501000` fails the backend's fixed `BAT-######` format and the run aborted). Fixed by widening to `BAT-95` + `{n:04d}`, good for pool sizes up to 9,999. The fix is backward-compatible: a pool of 200 generates the exact same battery IDs as before.
4. **Rung 4's throughput figure is likely understated.** It used a fixed 30-second window while median latency at that concurrency was already 7.2 seconds, so a meaningful fraction of the 20 users' requests were still in flight, uncounted, when the run was cut off. Rung 5 repeats the same concurrency with a 60-second window specifically to check this.

## Results

| Rung | Users | Pool size | Duration | Requests | Failures | Median | Average | Min | Max | p95 | p99 | Throughput |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Historical baseline* | 10 | 200 | ~20-30s | ~150 | 0 | 740ms | 1.65s | 233ms | 9.8s | 5.6s | 7.9s | ~520 events/sec |
| 1 | 1 | 5,000 | 30s | 26 | 0 | 710ms | 717ms | 659ms | 777ms | 770ms | 780ms | ~89 events/sec |
| 2 | 5 | 5,000 | 30s | 29 | 0 | 2.6s | 2.54s | 819ms | 5.2s | 5.2s | 5.2s | ~98 events/sec |
| 3 | 10 | 5,000 | 30s | 27 | 0 | 4.1s | 4.4s | 1.0s | 11.6s | 9.8s | 12.0s | ~92 events/sec |
| 4 | 20 | 5,000 | 30s | 17 | 0 | 7.2s | 6.9s | 1.4s | 13.4s | 13.0s | 13.0s | ~57 events/sec** |
| 5 | 20 | 5,000 | 60s | 40 | 0 | 12.0s | 14.5s | 1.6s | 35.3s | 32.0s | 35.0s | ~68 events/sec |

\* From the investigation that preceded this benchmark ladder (2026-09-23). Used a smaller battery pool (200, not isolated from possible row contention the way rungs 1-5 deliberately are), so it isn't a perfectly clean comparison against rungs 1-5 — but it shares the exact same concurrency as Rung 3 (10 users), which makes the contrast in Key Findings below worth having.

\** Likely undercounted — see caveat 4 above.

All 40+109 requests across every rung above succeeded (0 failures total). Percentiles and env details per rung are in the raw Locust output kept in the project's status notes for this phase.

## Database size at time of benchmark (captured between Rung 2 and Rung 3)

| Table | Row count |
|---|---|
| telemetry | 570,900 |
| alerts | 213,137 |
| batteries | 5,100 |
| battery_current_state | 4,948 |
| alembic_version | 1 |

## Key findings

1. **Zero failures across every rung**, including the slowest one where the tail request took 35.3 seconds. The harness itself held up under real, sustained use — this is 6.1's "done when" proving out in practice, not just in a single smoke test.
2. **Throughput is flat regardless of concurrency.** 1, 5, 10, and 20 concurrent users all land in the same rough band, roughly 60-100 events/sec. Adding more concurrent clients does not buy more throughput — it only adds queueing delay. Median latency scales with concurrency in a close-to-linear way (roughly 1x -> 3.7x -> 5.8x -> 10x -> 17x for 1/5/10/20/20 users respectively), which is the textbook signature of a fixed-capacity bottleneck sitting upstream of wherever concurrency is actually applied.
3. **The database connection pool is very likely not the bottleneck.** The backend's connection pool has a hard 30-second timeout (5 base connections + 10 overflow, from the Phase 5 investigation). Rung 5 had requests wait as long as 35.3 seconds with zero failures — past that timeout, with nothing erroring. If the pool were genuinely exhausted, that would surface as an explicit timeout error, not a slow-but-successful response. This rules out the leading suspect carried over from the Phase 5 investigation.
4. **The system has gotten significantly slower since the first measurements, independent of concurrency.** Comparing the historical baseline (10 users, ~520 events/sec) against Rung 3 (10 users, ~92 events/sec — identical concurrency) shows roughly a 5.6x drop in throughput at the same load level. The most likely cause is the database itself: `telemetry` had grown to 570,900 rows and `alerts` to 213,137 by this benchmark, versus a small fraction of that during the original trace. This reframes the investigation for 6.3 — the open question isn't only "how does the system handle concurrent traffic," but "why does ingestion get slower as the tables grow."

## What this means for 6.3 (Bottleneck Analysis)

Two of the roadmap's suggested areas to investigate can already be narrowed down from evidence, not assumption:

- **Connection pool — likely ruled out.** No failures even past its 30-second timeout, across the whole benchmark.
- **Current-state UPSERT / database writes — strong candidate.** Table growth correlates closely with the slowdown, and "flat throughput ceiling regardless of concurrency" is exactly the pattern you'd expect if requests are queueing behind something that processes one at a time and gets slower as the tables it touches grow (a lock, a single worker process, or a query whose cost scales with row count).
- **Still untested:** whether the backend is running with a single Uvicorn worker (meaning genuine parallel request processing may not be happening at all, independent of the connection pool's size), and how much of the per-request cost comes from the swollen `alerts` table specifically versus `telemetry`.

## 6.2 status

**Done.** The harness produced a real, reproducible curve across five load levels with zero request failures throughout, and the ladder was stopped once further escalation would have just repeated the same plateau pattern rather than surfacing anything new — matching the roadmap's own stopping condition ("stop when machine/environment limits make further testing meaningless").
