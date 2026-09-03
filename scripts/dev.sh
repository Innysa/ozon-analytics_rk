#!/usr/bin/env bash
# Local/Replit development: runs backend (auto-reload) and Vite dev server
# together. Frontend proxies /api to the backend (see frontend/vite.config.ts).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
fi
# shellcheck disable=SC1091
source backend/.venv/bin/activate
pip install -q -r backend/requirements.txt

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

alembic -c backend/alembic.ini upgrade head

(cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID' EXIT

(cd frontend && npm run dev -- --host 0.0.0.0 --port 5173)
