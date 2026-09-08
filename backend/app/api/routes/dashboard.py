from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.schemas.dashboard import DashboardOut
from app.services.dashboard_service import compute_dashboard

router = APIRouter(prefix="/api/stores/{store_id}/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
) -> DashboardOut:
    return compute_dashboard(db, store_id=ctx.store_id, date_from=date_from, date_to=date_to)
