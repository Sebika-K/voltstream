# VoltStream load-test harness (Roadmap 6.1)

A [Locust](https://locust.io) script that fires `POST /api/v1/telemetry/batch`
against a running backend, so VoltStream's real throughput, latency, and
error rate under load can be measured with real numbers instead of guessed.

Locust reports, per run: requests/sec, p50/p95/p99 response time, and error
rate -- covering four of the Roadmap's five load-test metrics directly. The
fifth (CPU / memory / database utilization) isn't something Locust measures;
watch it separately with `docker stats` and a Postgres query while a run is
in progress (see "Watching server-side resources" below). That's for 6.2's
benchmark runs, not something this harness needs to do itself.

## What it does

- Registers a dedicated pool of load-test batteries (`BAT-950000` upward --
  a range nothing else in the project uses) once, before any load starts.
- Every simulated user repeatedly sends one full batch of freshly-generated,
  valid telemetry (default 100 events, matching the simulator's own default
  batch size) to a random battery from that pool, with no pause between
  requests.
- How much load that adds up to is controlled entirely by Locust's own
  `--users` / `--spawn-rate` flags -- not by anything in `locustfile.py`.

## One-time setup

```
cd loadtest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Before a real benchmark run: stop the simulator

The load-test harness uses its own batteries, so it can run alongside the
simulator without erroring. But for a *measurement* you actually trust, stop
the simulator first -- otherwise its steady ~100 events/sec of real traffic
is mixed into whatever this harness reports, and the number stops meaning
"what this harness's workload achieved."

```
docker compose stop simulator
```

(Bring it back afterwards with `docker compose start simulator`.)

## Running it

A short smoke test, low load, with Locust's web UI so you can watch it live:

```
locust -f locustfile.py --host http://localhost:8000
```

Then open http://localhost:8089, set a number of users and a spawn rate, and
click Start.

A specific, repeatable, headless run -- this is the form to use for anything
you want to record and compare later (6.2's baseline, 6.4's before/after):

```
locust -f locustfile.py --host http://localhost:8000 \
  --headless --users 50 --spawn-rate 10 --run-time 60s \
  --csv results/50users
```

That writes `results/50users_stats.csv`, `results/50users_stats_history.csv`,
and `results/50users_failures.csv` -- permanent, comparable records of the
run. `results/` is already created and gitignored-by-convention for these
throwaway CSVs (nothing to commit unless you want to keep a specific run's
numbers).

The final summary Locust prints (or the CSV's last row) gives:

- **requests/sec** for `/api/v1/telemetry/batch` -- multiply by
  `LOADTEST_BATCH_SIZE` (default 100) to get **events/sec**, the number the
  Roadmap actually asks for.
- **p50 / p95 / p99** response time in milliseconds.
- **failure count** -- should be 0 for a healthy run; anything else means the
  backend rejected or errored on a batch (check the backend's own logs for
  `request_rejected` or `request_failed` to see why).

## Configuration

| Environment variable | Default | What it controls |
|---|---|---|
| `LOADTEST_BATCH_SIZE` | `100` | Events per batch request -- set this to compare batch sizes (Roadmap 6.4) |
| `LOADTEST_BATTERY_POOL_SIZE` | `200` | How many distinct batteries the traffic spreads across |

Example, testing a bigger batch size:

```
LOADTEST_BATCH_SIZE=500 locust -f locustfile.py --host http://localhost:8000 \
  --headless --users 50 --spawn-rate 10 --run-time 60s --csv results/500batch
```

## Watching server-side resources

While a headless run is going (in a second terminal):

```
docker stats --no-stream voltstream-backend voltstream-postgres
```

For database-side load, a quick connection-count check (the same query used
during the Phase 5 failure tests):

```
docker compose exec postgres psql -U voltstream -d voltstream \
  -c "select count(*) as connections from pg_stat_activity;"
```

## Increasing the load ceiling

Locust itself, run this way, is single-process: at very high `--users`
counts the bottleneck can become Locust's own machine rather than VoltStream.
If requests/sec stops climbing while your own machine's CPU is pegged (check
Activity Monitor), that's Locust running out of headroom, not a VoltStream
finding -- Locust supports a distributed `--master` / `--worker` mode for
that case, not needed until load testing goes well beyond what one process
can generate.
