"""Database access: async engine/session management and the shared declarative base.

Schema changes are managed exclusively through Alembic migrations (see ``backend/alembic``)
-- ``Base.metadata.create_all()`` is intentionally not used to create or evolve schema.
"""
