#!/usr/bin/env bash
# Single-command production/Replit entry point: installs dependencies, builds
# the frontend, applies migrations, and serves the whole app (API + built SPA)
# from one uvicorn process on $PORT.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
fi
# shellcheck disable=SC1091
source backend/.venv/bin/activate
pip install -q -r backend/requirements.txt

(cd frontend && npm install --no-audit --no-fund && npm run build)

alembic -c backend/alembic.ini upgrade head

cd backend
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
