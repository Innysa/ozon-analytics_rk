from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.schemas.analytics import ReviewAnalyticsOut
from app.services.analytics_service import compute_review_analytics

router = APIRouter(prefix="/api/stores/{store_id}/analytics", tags=["analytics"])


@router.get("/reviews", response_model=ReviewAnalyticsOut)
def review_analytics(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
) -> ReviewAnalyticsOut:
    return compute_review_analytics(db, store_id=ctx.store_id, product_id=product_id)
