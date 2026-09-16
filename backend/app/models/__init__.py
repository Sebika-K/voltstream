"""ORM models package.

Importing this package registers every model's table onto the shared
`Base.metadata` (see `app.db.base`) -- that's what lets `alembic revision
--autogenerate` and `alembic upgrade` see them. `alembic/env.py` imports this
package for exactly that reason; nothing in the application itself needs to
import it directly.
"""

from __future__ import annotations

from app.models.alert import Alert
from app.models.battery import Battery
from app.models.battery_current_state import BatteryCurrentState
from app.models.telemetry import Telemetry

__all__ = ["Alert", "Battery", "BatteryCurrentState", "Telemetry"]
