from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.membership import StoreRole
from app.schemas.advertising import AdvertisingCampaignOut

router = APIRouter(prefix="/api/stores/{store_id}/advertising", tags=["advertising"])


@router.get("/campaigns", response_model=list[AdvertisingCampaignOut])
def list_campaigns(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)
) -> list[AdvertisingCampaignOut]:
    """Campaign metadata synced from Ozon Performance API (see
    app.services.ozon_performance.client). Returns an empty list — never
    fabricated data — until a sync has actually run for this store."""
    campaigns = db.scalars(
        select(AdvertisingCampaign).where(AdvertisingCampaign.store_id == ctx.store_id).order_by(AdvertisingCampaign.name)
    ).all()
    return [AdvertisingCampaignOut.model_validate(c) for c in campaigns]
