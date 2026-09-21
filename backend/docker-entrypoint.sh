#!/bin/sh
# Container startup for the backend.
#
# 1. Bring the database schema up to date (Alembic is the project's only
#    schema mechanism -- the app never calls create_all()). On a brand-new
#    database this creates every table; on an existing one it does nothing.
# 2. Start the API server. `exec` makes uvicorn the main process, so Docker's
#    stop signal (SIGTERM) reaches it directly and it shuts down cleanly.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
