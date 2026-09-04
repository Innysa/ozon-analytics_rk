#!/usr/bin/env bash
# Container entrypoint: apply pending Alembic migrations, then serve API +
# built SPA from a single uvicorn process. Runs from WORKDIR /app; PYTHONPATH
# (set in the Dockerfile) points at backend/, so both alembic and `app.*`
# imports resolve regardless of the current directory.
set -euo pipefail

alembic -c backend/alembic.ini upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
