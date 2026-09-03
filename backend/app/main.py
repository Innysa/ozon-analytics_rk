from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    advertising,
    ai_settings,
    analytics,
    auth,
    change_history,
    ozon_connection,
    ozon_performance,
    products,
    reviews,
    review_upload,
    stores,
    sync,
    users,
)
from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(stores.router)
app.include_router(ozon_connection.router)
app.include_router(ozon_performance.router)
app.include_router(advertising.router)
app.include_router(sync.router)
app.include_router(review_upload.router)
app.include_router(reviews.router)
app.include_router(ai_settings.router)
app.include_router(analytics.router)
app.include_router(products.router)
app.include_router(change_history.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "demo_mode": settings.DEMO_MODE}


# Serve the built frontend (frontend/dist) as static files + SPA fallback, so
# the whole app runs from a single `uvicorn app.main:app` process in Replit.
_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")
