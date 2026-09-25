"""Prometheus-style application metrics (Roadmap 7.1, PRD section 11 /
Contract's observability requirements).

Every metric is a plain module-level object, the "define once, import and
use" pattern `prometheus_client` expects -- keeping every metric name and
label set visible here in one place, rather than scattered across whichever
service happens to touch it.

Deliberately unlabeled, or labeled only by a small fixed set of values
(`code`, `operation`, `endpoint`, `method`, `severity`) -- never by
`battery_id`. With up to 10,000 simulated batteries (PRD section 9's target
scale), a per-battery label on any of these would create tens of thousands
of individual time series, exactly the "cardinality explosion" Prometheus's
own docs warn against. Anything that needs a per-battery view already has
one -- the product API and dashboard; these metrics answer "how is the
*system* doing," not "how is battery X doing."

Roadmap 7.2 (an actual Prometheus server) is optional and a separate item --
but exposing metrics in Prometheus's own text-exposition format from the
start, instead of inventing an ad-hoc JSON shape now and converting later,
is what makes 7.2, if it happens, a matter of pointing a Prometheus server
at `GET /metrics` rather than reworking this module.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# -- Telemetry ingestion (app/services/telemetry_service.py) ---------------

TELEMETRY_RECEIVED = Counter(
    "voltstream_telemetry_received_total",
    "Telemetry events received, before dedup (every event in every request "
    "that reached the ingestion service).",
)
TELEMETRY_INSERTED = Counter(
    "voltstream_telemetry_inserted_total",
    "Telemetry events actually inserted (newly stored, not a duplicate retry).",
)
TELEMETRY_DUPLICATES = Counter(
    "voltstream_telemetry_duplicates_total",
    "Telemetry events skipped because their event_id was already stored.",
)

# -- Errors (app/core/errors.py) --------------------------------------------

ERRORS = Counter(
    "voltstream_errors_total",
    "Requests that ended in an error response, labeled by error code "
    "(BATTERY_NOT_FOUND, BATCH_TOO_LARGE, DATABASE_UNAVAILABLE, ...).",
    ["code"],
)

# -- Latency ------------------------------------------------------------

INGESTION_LATENCY_SECONDS = Histogram(
    "voltstream_ingestion_latency_seconds",
    "Time spent inside the telemetry ingestion service function -- "
    "validation, database work, and rule evaluation together -- labeled by "
    "which endpoint shape (single vs. batch).",
    ["endpoint"],
)
DATABASE_LATENCY_SECONDS = Histogram(
    "voltstream_database_latency_seconds",
    "Time spent inside one specific database statement on the ingestion "
    "path, labeled by which statement (telemetry_insert vs. "
    "current_state_upsert) -- this is what Roadmap 6.3's bottleneck "
    "analysis had to infer from pg_stat_activity; going forward it's a "
    "metric.",
    ["operation"],
)

# -- Fleet / alerts / predictions --------------------------------------------

ACTIVE_BATTERIES = Gauge(
    "voltstream_active_batteries",
    "Batteries whose current-state row is not OFFLINE. Refreshed "
    "periodically by app/services/metrics_refresher.py, not computed on "
    "every request.",
)
ALERTS_ACTIVE = Gauge(
    "voltstream_alerts_active",
    "Currently unresolved alerts, by severity. Refreshed periodically by "
    "app/services/metrics_refresher.py.",
    ["severity"],
)
PREDICTIONS = Counter(
    "voltstream_predictions_total",
    "Depletion predictions served, labeled by method (baseline, ml, or "
    "unavailable when no prediction could be made).",
    ["method"],
)
