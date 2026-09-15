"""add battery_current_state table

Revision ID: ccea1f615ff0
Revises: b682f1e101a5
Create Date: 2026-09-15

Roadmap step 2.1: a second, always-latest-only table alongside `telemetry`'s
append-only history, so "what is every battery doing right now" is a single
cheap lookup instead of a per-battery scan over full history. Matches the
Implementation Contract section 20 database schema contract's
`battery_current_state` column set exactly. Rows are written and overwritten
by telemetry ingestion (app/services/telemetry_service.py), never by this
migration -- a freshly created table starts empty, and a registered battery
that hasn't reported telemetry yet is expected to have no row here (Contract
section 21).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "ccea1f615ff0"
down_revision: Union[str, None] = "b682f1e101a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "battery_current_state",
        sa.Column("battery_id", sa.String(length=32), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_of_charge", sa.Double(), nullable=False),
        sa.Column("temperature_c", sa.Double(), nullable=False),
        sa.Column("power_kw", sa.Double(), nullable=False),
        sa.Column("health_percent", sa.Double(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["battery_id"], ["batteries.battery_id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("battery_current_state")
