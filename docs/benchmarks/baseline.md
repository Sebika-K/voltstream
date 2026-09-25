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

## 6.3 Bottleneck Analysis

**Roadmap item:** 6.3 Bottleneck Analysis (Phase 6 — Performance Engineering)
**Date:** 2026-09-24

### Method

Building on the two open leads from 6.2 (connection pool ruled out; current-state upsert and worker count still suspect), two checks were run against the same 10-user / 30-second load level used throughout the baseline: how many Uvicorn worker processes the backend actually runs, and what the backend's database connections are actually waiting on while a load test is in flight (via Postgres's own `pg_stat_activity`), cross-checked against the backend's own structured logs for how long it reports spending on each batch.

### Finding 1 — single Uvicorn worker

The backend's entrypoint (`backend/docker-entrypoint.sh`) starts it with `exec uvicorn app.main:app --host 0.0.0.0 --port 8000` — no `--workers` flag anywhere in the Dockerfile, entrypoint script, or compose file. Uvicorn defaults to a single worker process when none is specified, so the entire backend runs as one process handling every request. (`SEND_WORKERS` in the compose file is unrelated — that's the simulator's own sender-pool setting from Phase 5.4, not the backend's.)

### Finding 2 — the backend's own reported processing time is itself huge

Tailing the backend's structured logs (`docker compose logs -f backend | grep telemetry_batch_processed`) during a load test showed `duration_ms` values as high as 44,842ms, 47,617ms, and 56,344ms for a single 100-event batch — the backend's own internal timer, not client-observed latency. This rules out a simple "requests queue outside the backend before any work starts" explanation: if that were the whole story, the reported processing time would stay close to the original ~165-178ms trace from Phase 5.2 once a request actually began executing. Instead, the slowness is happening inside the measured work itself.

### Finding 3 — direct confirmation via `pg_stat_activity`

Querying Postgres's own activity view during a fresh 10-user run showed the mechanism directly. Repeated snapshots showed multiple backend sessions simultaneously `active`, `wait_event_type = Lock`, `wait_event = transactionid`, all running the same statement: `INSERT INTO battery_current_state (...)`. The same sessions were tracked growing older across consecutive snapshots taken a few seconds apart (one session's wait grew from 6.9s -> 14.4s -> 18.9s across three snapshots), confirming a real, growing backlog rather than a transient blip.

A second detail explains why the wait keeps growing instead of staying short: some sessions showed `idle in transaction` while sitting on a `SELECT ... FROM alerts` query in the same session — meaning the code holds the same open transaction, and the row locks that come with it, across both the `battery_current_state` upsert and subsequent alert-related work, rather than releasing the lock as soon as the upsert itself finishes. With `alerts` now holding 213,000+ rows (see 6.2's caveat about the harness's synthetic data over-triggering alerts), that alert-related work inside the same transaction is itself slow, which stretches out exactly how long every other concurrent request touching the same battery rows has to wait.

### Conclusion

**The bottleneck is row-lock contention on `battery_current_state`, caused by the current-state upsert holding its transaction — and the row locks that come with it — open across more work than the upsert itself needs**, specifically alert-related work against a now-oversized `alerts` table. Any two concurrent requests that happen to touch even one of the same battery rows (not rare with the pool sizes and concurrency used in this investigation) queue behind each other for the full duration of that combined transaction, not just the brief moment of the actual row update. This explains every pattern observed in 6.2: the flat throughput ceiling regardless of concurrency (only one transaction per contended row can make progress at a time), the near-linear latency growth with concurrency (each additional contending request adds another full transaction's wait to the queue), and the complete absence of connection-pool timeouts even past 35 seconds of wait (the connections aren't waiting for a free slot in the pool — they already have one, and are waiting on a Postgres-level lock while holding it).

The single Uvicorn worker and the connection pool are both real, verified facts about the system, but neither is the primary driver here — a single worker can still run many requests concurrently as long as they are genuinely waiting on I/O rather than on each other, and the pool never actually ran out across any run in this investigation. The lock contention is what is actually gating throughput.

### 6.3 status

**Done.** A bottleneck was identified from direct measurement — `pg_stat_activity` catching multiple sessions queued on the same lock, growing older in real time, tied to a specific query — rather than from assumption. Ready for 6.4 to test a fix against this same baseline. A leading candidate: keep alert evaluation and alert writes out of the same transaction as the current-state upsert, so the row lock is held only as long as the upsert itself takes.

## 6.4 Fix Verification

**Roadmap item:** 6.4 Fix Verification (Phase 6 — Performance Engineering)
**Date:** 2026-09-24

### The fix

Following 6.3's leading candidate directly: `telemetry_service.py`'s ingestion path now commits the `telemetry` insert and `battery_current_state` upsert on their own, before alert evaluation runs, instead of holding one open transaction across both. This shrinks the row-lock hold window from "however long the whole rule pass takes" down to "however long the upsert itself takes" — the row locks that were being held across the slow, now-oversized `alerts` table work are released as soon as current-state is written. Trade-off, accepted deliberately: telemetry storage and alert evaluation are no longer atomic with each other. If rule evaluation fails after the commit, the battery's telemetry and current-state are already saved rather than rolled back — judged better than losing real device data over a rule bug, and not a permanent gap, since the next reading for the same battery evaluates the same rules again against its own newest state.

### Bug found while testing the fix: `create_or_retain_alert` race condition

Shortening the lock window made an existing, previously-latent bug in `anomaly_detection_service.py::create_or_retain_alert` much easier to hit: it was a check-then-insert (SELECT for an unresolved alert, then either UPDATE or INSERT), not atomic. Two concurrent callers for the same `battery_id` + `alert_type` — a burst of alert-triggering events for one battery, or this rule-evaluation path racing the offline-detector's `DEVICE_OFFLINE` path through the same shared helper — could both see "no unresolved alert yet" and both attempt an INSERT, and the second would violate the partial unique index (`ux_alerts_battery_id_alert_type_unresolved`) and raise instead of retaining.

Fixed the same way Phase 5 fixed the equivalent `battery_current_state` problem: a single atomic `INSERT ... ON CONFLICT (battery_id, alert_type) ... DO UPDATE`, conflict target scoped to unresolved rows only (`index_where`), updating only `severity`/`message`/`measured_value`/`threshold_value` so `alert_id` and the original incident `timestamp` stay untouched on retain — unchanged behavior from before, just race-free.

One follow-up bug surfaced by actually running this against Postgres (not caught by the sandbox's `py_compile`-only check): the index's own predicate is `resolved = false` (an equality op), but SQLAlchemy's `Alert.resolved.is_(False)` compiled the conflict clause as `resolved IS false` (a boolean test). Postgres requires the ON CONFLICT predicate to structurally match the index predicate to infer a conflict target, and does not treat those two forms as equivalent for that purpose, even though they're semantically identical — every write failed with "there is no unique or exclusion constraint matching the ON CONFLICT specification" until `index_where` was given as raw SQL text (`resolved = false`) matching the index definition exactly.

### Verification

1. **Full backend test suite: 218/218 passed** (`pytest -v`), including a new concurrency test (`test_concurrent_low_soc_events_for_one_battery_retain_a_single_alert`, `tests/test_anomaly_detection.py`) that fires 20 concurrent LOW_SOC-triggering requests at the same battery and asserts exactly one alert survives.
2. **Live load test, same shape that originally broke it:** 20 users / 60s against the rebuilt backend, simulator stopped. 268 requests, 0 failures. No `ON CONFLICT`/`IntegrityError` traces anywhere in the backend log.
3. **The second race path confirmed live, not just in theory:** the offline detector fired mid-run (`Marked 200 battery(s) offline`) while load-test batches were still landing — the exact "rule-evaluation path racing the offline-detector's DEVICE_OFFLINE path through the same helper" scenario the fix targets — and completed without error.
4. One unrelated `database_unavailable` ERROR appeared once in that run (a `TimeoutError` on the `/ready` healthcheck's own connection-pool probe, 20 users against the pool's 15 connections). This is the Phase 5 controlled-503-under-pool-exhaustion behavior (`core/errors.py`) working as designed, not the bug this item was verifying — flagged here as a separate thread for whenever Phase 6 performance work continues, not a 6.4 finding.

### Before/After Benchmark

The Roadmap's actual Done-When for 6.4 ("Optimization Experiments") asks for a documented before/after benchmark, not just a correctness check. So the exact same first four rungs from 6.2 -- 1/5/10/20 users, 5,000-battery pool, 30-second headless runs, simulator stopped -- were rerun against the fixed backend, unmodified from the 6.2 methodology. The database was not reset (consistent with 6.2's own approach), so if anything this is a harder test than the original: `telemetry` and `alerts` are both larger now than they were during 6.2.

| Rung | Users | Requests | Failures | Median | Average | Min | Max | p95 | p99 | Throughput |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 (before) | 1 | 26 | 0 | 710ms | 717ms | 659ms | 777ms | 770ms | 780ms | ~89 events/sec |
| 1 (after) | 1 | 60 | 0 | 220ms | 221ms | 164ms | 315ms | 280ms | 320ms | ~204 events/sec |
| 2 (before) | 5 | 29 | 0 | 2.6s | 2.54s | 819ms | 5.2s | 5.2s | 5.2s | ~98 events/sec |
| 2 (after) | 5 | 68 | 0 | 1.2s | 1.34s | 660ms | 3.2s | 2.7s | 3.2s | ~229 events/sec |
| 3 (before) | 10 | 27 | 0 | 4.1s | 4.4s | 1.0s | 11.6s | 9.8s | 12.0s | ~92 events/sec |
| 3 (after) | 10 | 52 | 0 | 2.3s | 2.96s | 831ms | 6.4s | 6.1s | 6.4s | ~176 events/sec |
| 4 (before) | 20 | 17 | 0 | 7.2s | 6.9s | 1.4s | 13.4s | 13.0s | 13.0s | ~57 events/sec* |
| 4 (after) | 20 | 44 | 0 | 4.8s | 5.11s | 1.6s | 11.8s | 10.0s | 12.0s | ~148 events/sec |

\* Rung 4's "before" figure was already flagged in 6.2 as likely undercounted (a 30-second window cut off requests still in flight at that median latency), so the true improvement at 20 users is probably larger than this table shows.

At every concurrency level, roughly 2-2.5x more requests completed in the same 30-second window, and median latency dropped 33-69%. The improvement holds (and if anything grows) at low concurrency -- 1 user went from 710ms to 220ms median -- which is the interesting part: at 1 user there is no lock contention to relieve, so the transaction-shortening change alone can't explain that gain. The other half of the fix does: the old `create_or_retain_alert` was a SELECT followed by a separate INSERT-or-UPDATE (two round trips to the database per alert type per event), while the new atomic `INSERT ... ON CONFLICT ... DO UPDATE` is one. With four rules evaluated per event, that's up to 8 statements collapsing to 4 -- a real reduction in work done per request, independent of concurrency, layered on top of the lock-contention fix that was 6.3's original hypothesis. Throughput does still taper off somewhat as concurrency rises (204 -> 148 events/sec from 1 to 20 users) rather than staying perfectly flat, meaning some contention-driven ceiling remains -- expected, since the current-state upsert itself still takes a row lock -- but it is far higher and far less punishing than the original flat ~57-98 events/sec ceiling that didn't move at all with concurrency.

Raw Locust output for these four runs is in `loadtest/results/afterfix_*users_stats.csv` (gitignored, not committed -- see the original rungs' own CSVs for the "before" comparison point, generated the same way during 6.2).

### 6.4 status

**Done.** The 6.3 root cause (lock held across alert-related work) is fixed, and fixing it surfaced and closed a second, previously-latent bug (`create_or_retain_alert`'s non-atomic check-then-insert) that shortening the lock window made much easier to trigger. Verified at three levels: unit/integration tests, a live load test at the same concurrency that originally produced the failures, and a live reproduction of the specific dual-path race (telemetry rules vs. offline detector) the fix targets. The Roadmap's own Done-When for this item -- a documented before/after benchmark showing a meaningful improvement -- is satisfied above: 2-2.5x more throughput and 33-69% lower median latency at every rung tested, against a database that had only grown larger since the original baseline.

