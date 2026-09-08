"""Shared SQLAlchemy declarative base.

No application models exist yet (Task 1 is the project foundation only). This base is
still required infrastructure: Alembic's ``env.py`` imports ``Base.metadata`` as its
migration target so that ``alembic revision --autogenerate`` works correctly once the
first ORM models are added in a later phase.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class every future ORM model will inherit from."""
