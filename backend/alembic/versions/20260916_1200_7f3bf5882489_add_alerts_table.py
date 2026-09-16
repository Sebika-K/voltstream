"""add alerts table

Revision ID: 7f3bf5882489
Revises: ccea1f615ff0
Create Date: 2026-09-16

Roadmap step 3.3: the first table for anomaly detection. Matches the
Implementation Contract section 20 database schema contract's `alerts`
column set exactly. Rows are written by
`app/services/anomaly_detection_service.py` (rule-triggered alerts, during
telemetry ingestion) and `app/services/offline_detector.py`
(`DEVICE_OFFLINE` alerts, from the periodic offline-detection loop) --
never by this migration, which only creates the empty table.

Two indexes beyond the primary key, both earning their place from how this
table is actually queried, not added speculatively:

- `(battery_id, alert_type) WHERE resolved = false` is a **partial unique**
  index, not just a lookup index -- it's the database-level enforcement of
  Contract section 26's rule ("only one unresolved alert of a given
  alert_type MAY exist for a battery at a time"). The application logic in
  `app/services/anomaly_detection_service.py` already checks for an existing
  unresolved alert before creating one, but that check-then-insert isn't
  atomic on its own; this constraint is what makes the invariant hold even
  if application logic ever has a bug, rather than relying on it never
  happening to be true.
- `(resolved)` alone backs the fleet-wide "how many alerts are currently
  active" count (`GET /api/v1/fleet/summary`'s `active_alerts`/
  `critical_alerts`, Roadmap 2.4) once that endpoint starts reading from
  this table instead of returning a hardcoded 0.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7f3bf5882489"
down_revision: Union[str, None] = "ccea1f615ff0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("battery_id", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("measured_value", sa.Double(), nullable=True),
        sa.Column("threshold_value", sa.Double(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["battery_id"], ["batteries.battery_id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ux_alerts_battery_id_alert_type_unresolved",
        "alerts",
        ["battery_id", "alert_type"],
        unique=True,
        postgresql_where=sa.text("resolved = false"),
    )
    op.create_index("ix_alerts_resolved", "alerts", ["resolved"])


def downgrade() -> None:
    op.drop_index("ix_alerts_resolved", table_name="alerts")
    op.drop_index("ux_alerts_battery_id_alert_type_unresolved", table_name="alerts")
    op.drop_table("alerts")
