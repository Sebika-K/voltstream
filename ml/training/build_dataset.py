"""Build the ML training dataset from stored telemetry (Roadmap 4.2,
Contract sections 41-42).

Run it from the `ml/` directory:

    python training/build_dataset.py

It connects to the same PostgreSQL database the backend uses, pulls every
battery's telemetry history, and for each individual telemetry reading
decides whether that reading is usable as a training example. A reading
only becomes one row of the dataset if ALL four of these hold (Contract
section 42, checked in this exact order):

  1. its state_of_charge is above the 20% critical threshold -- a battery
     already at/below critical has nothing left to predict.
  2. the battery is actually DISCHARGING at that moment -- charging/idle/
     fault readings aren't depleting, so "time to critical" isn't a
     meaningful question for them (matches Roadmap 4.1's own eligibility
     rule for the physics baseline).
  3. there's a full 15-minute lookback of history behind it, not just a
     partial one -- otherwise the rolling-window features
     (`rolling_power_15m` etc., see `ml/features.py`) would be computed
     from an artificially short window and look like a real signal when
     they're really just "this battery hasn't been reporting very long."
  4. looking *forward* from that reading, this battery's SOC does drop to
     <=20% at some later point still in the stored history.

If (4) never happens -- the battery recharges before going critical again,
or the stored history simply ends first -- the reading is thrown away
entirely. Contract section 42 is explicit that these MUST NOT be given a
made-up target value; there is no "close enough" answer for "how long
until critical" when critical never actually happened.

For every row that IS eligible, the target is:

    minutes_to_critical = (timestamp of that future <=20% reading) - (this reading's timestamp)

and the input features are computed by `compute_features_for_point`
(`ml/features.py`) -- the exact same function Roadmap 4.5 will call again
at live prediction time, per Contract section 41's training/serving-skew
requirement.

Output: `ml/data/training_dataset.csv`, plus a printed summary of how many
readings were considered, how many became training rows, and why the rest
were excluded -- so a re-run is fully reproducible and its result is never
a silent number with no explanation behind it.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

# So `from features import ...` resolves to ml/features.py regardless of
# which directory this script is actually invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import FeatureRow, MAX_HISTORY_WINDOW_MINUTES, compute_features_for_point  # noqa: E402

# sqlalchemy/psycopg2 are only needed by the three DB-facing functions below
# (_load_database_url, _load_batteries, _load_telemetry) -- imported lazily
# inside them rather than at module load time, so the actual dataset-building
# logic (_build_rows_for_battery, _next_critical_index) stays importable and
# unit-testable (see ml/tests/test_build_dataset.py) on a machine that has
# pandas/numpy but hasn't installed the database driver yet.

# Contract section 39/42: the SOC level a discharging battery is predicted
# to be counting down to. This intentionally duplicates the backend's
# CRITICAL_SOC_PERCENT setting (app/core/config.py) rather than importing
# it -- this script has no dependency on the backend package at all, by
# design (it's a standalone, separately-run data pipeline, matching how
# `simulator/` and `backend/` are already separate components with their
# own dependencies). Roadmap 4.5 (serving the model from inside the
# backend) is where these two numbers actually need to be reconciled;
# noted here so that reconciliation isn't forgotten.
CRITICAL_SOC_PERCENT = 20.0

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "training_dataset.csv"

FEATURE_COLUMNS = [f.name for f in dataclasses.fields(FeatureRow)]


def _load_database_url() -> str:
    """Read DATABASE_URL from the project's .env, same file the backend and
    Alembic both already use, then swap the async driver for a sync one.

    TDD section 15 is explicit that CPU-heavy/offline work like this must
    not run inside the backend's async request-handling engine -- this
    script is a standalone process, so it uses a plain synchronous
    SQLAlchemy engine (psycopg2) instead of the backend's asyncpg one.
    """
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError(f"DATABASE_URL is not set (checked {ENV_PATH})")
    return raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def _load_batteries(engine) -> pd.DataFrame:
    from sqlalchemy import text

    query = text("SELECT battery_id, capacity_kwh, profile_type FROM batteries")
    return pd.read_sql_query(query, engine)


def _load_telemetry(engine) -> pd.DataFrame:
    from sqlalchemy import text

    query = text(
        "SELECT battery_id, timestamp, state_of_charge, power_kw, "
        "temperature_c, health_percent, status "
        "FROM telemetry ORDER BY battery_id, timestamp"
    )
    return pd.read_sql_query(query, engine, parse_dates=["timestamp"])


def _next_critical_index(soc: np.ndarray) -> np.ndarray:
    """For every position i, the smallest index j > i where soc[j] <=
    CRITICAL_SOC_PERCENT, or -1 if no such j exists.

    A single backward pass (O(n)) rather than scanning forward from every
    point separately (which would be O(n^2) for a battery with a long
    history) -- as we walk from the end toward the start, the "nearest
    critical position to the right" is exactly what the previous iteration
    already figured out, updated only when the current position itself
    turns out to be a critical one.
    """
    n = len(soc)
    next_idx = np.full(n, -1, dtype=np.int64)
    last_critical_pos = -1
    for i in range(n - 1, -1, -1):
        next_idx[i] = last_critical_pos
        if soc[i] <= CRITICAL_SOC_PERCENT:
            last_critical_pos = i
    return next_idx


def _build_rows_for_battery(
    battery_id: str,
    telemetry: pd.DataFrame,
    *,
    capacity_kwh: float,
    profile_type: str,
    stats: Counter,
) -> list[dict]:
    """One battery's full telemetry history in, its list of eligible
    training rows out. `telemetry` must already be sorted by timestamp
    ascending."""
    df = telemetry.reset_index(drop=True)
    n = len(df)
    if n == 0:
        return []

    soc = df["state_of_charge"].to_numpy()
    status = df["status"].to_numpy()
    ts = df["timestamp"]
    next_critical_idx = _next_critical_index(soc)

    lookback = pd.Timedelta(minutes=MAX_HISTORY_WINDOW_MINUTES)
    window_start = 0  # left edge of the trailing lookback window; only ever moves forward
    rows: list[dict] = []

    for i in range(n):
        as_of = ts.iat[i]

        # 1. current SOC must be above the critical threshold.
        if soc[i] <= CRITICAL_SOC_PERCENT:
            stats["already_at_or_below_critical"] += 1
            continue

        # 2. the battery must actually be discharging right now.
        if status[i] != "DISCHARGING":
            stats["not_discharging"] += 1
            continue

        # advance the window's left edge so df.iloc[window_start] is the
        # oldest reading still within the trailing lookback of `as_of`.
        while ts.iat[window_start] < as_of - lookback:
            window_start += 1

        # 3. that lookback must be FULLY populated -- this battery's very
        # first-ever reading has to be at least `lookback` before `as_of`,
        # otherwise the rolling-window features would be computed over
        # whatever partial history happens to exist, not a real 15-minute
        # window.
        if (as_of - ts.iat[0]) < lookback:
            stats["insufficient_history"] += 1
            continue

        # 4. some future reading for this same battery must actually reach
        # the critical threshold -- otherwise there is no true answer to
        # "how long until critical" to use as a training target.
        j = next_critical_idx[i]
        if j == -1:
            stats["no_future_critical_point"] += 1
            continue

        window = df.iloc[window_start : i + 1]
        features = compute_features_for_point(window, capacity_kwh=capacity_kwh, profile_type=profile_type)
        minutes_to_critical = (ts.iat[j] - as_of).total_seconds() / 60.0

        stats["eligible"] += 1
        rows.append(
            {
                "battery_id": battery_id,
                "timestamp": as_of,
                **dataclasses.asdict(features),
                "minutes_to_critical": minutes_to_critical,
            }
        )

    return rows


def build_dataset(engine) -> tuple[pd.DataFrame, Counter]:
    batteries = _load_batteries(engine).set_index("battery_id")
    telemetry = _load_telemetry(engine)

    stats: Counter = Counter()
    all_rows: list[dict] = []

    for battery_id, group in telemetry.groupby("battery_id", sort=False):
        if battery_id not in batteries.index:
            # Telemetry for a battery that's since been deleted from
            # `batteries` -- shouldn't happen given the foreign key, but
            # skipped explicitly rather than crashing the whole build.
            stats["unknown_battery"] += len(group)
            continue
        meta = batteries.loc[battery_id]
        rows = _build_rows_for_battery(
            battery_id,
            group,
            capacity_kwh=float(meta["capacity_kwh"]),
            profile_type=str(meta["profile_type"]),
            stats=stats,
        )
        all_rows.extend(rows)

    stats["total_readings_considered"] = len(telemetry)
    dataset = pd.DataFrame(all_rows, columns=["battery_id", "timestamp", *FEATURE_COLUMNS, "minutes_to_critical"])
    return dataset, stats


def main() -> None:
    from sqlalchemy import create_engine

    database_url = _load_database_url()
    engine = create_engine(database_url)

    print(f"Connecting to {database_url.split('@')[-1]} ...")
    dataset, stats = build_dataset(engine)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(OUTPUT_PATH, index=False)

    print()
    print("Dataset build summary")
    print("----------------------")
    print(f"Telemetry readings considered:      {stats['total_readings_considered']}")
    print(f"  already at/below critical SOC:     {stats['already_at_or_below_critical']}")
    print(f"  not discharging:                   {stats['not_discharging']}")
    print(f"  insufficient history (<15 min):    {stats['insufficient_history']}")
    print(f"  no future critical point reached:  {stats['no_future_critical_point']}")
    if stats["unknown_battery"]:
        print(f"  telemetry for unknown battery_id:  {stats['unknown_battery']}")
    print(f"Eligible training rows written:     {stats['eligible']}")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
