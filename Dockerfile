# Multi-stage build: compile the frontend SPA, then serve it together with
# the FastAPI backend from a single lean runtime image. Mirrors the layout
# scripts/start.sh already uses for Replit Deployment (one uvicorn process
# serves both /api and the built SPA — see app/main.py's static mount).

# ---- Stage 1: build the frontend (React + TS + Vite) ----
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: backend runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/backend

WORKDIR /app

# curl only for the container HEALTHCHECK; psycopg[binary] already bundles
# libpq, so no extra system client libraries are required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY migrations/ migrations/
COPY --from=frontend-build /app/frontend/dist frontend/dist

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
