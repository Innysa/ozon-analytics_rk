from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.store_ai_settings import StoreAISettings
from app.models.user import User
from app.schemas.ai_settings import StoreAISettingsOut, StoreAISettingsUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/api/stores/{store_id}/ai-settings", tags=["ai-settings"])


def _get_or_create(db: Session, store_id: str) -> StoreAISettings:
    settings = db.query(StoreAISettings).filter(StoreAISettings.store_id == store_id).first()
    if not settings:
        settings = StoreAISettings(store_id=store_id)
        db.add(settings)
        db.flush()
    return settings


@router.get("", response_model=StoreAISettingsOut)
def get_ai_settings(ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)) -> StoreAISettingsOut:
    settings = _get_or_create(db, ctx.store_id)
    db.commit()
    return StoreAISettingsOut.model_validate(settings)


@router.put("", response_model=StoreAISettingsOut)
def update_ai_settings(
    payload: StoreAISettingsUpdate,
    ctx: StoreContext = Depends(require_store_role(StoreRole.OWNER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StoreAISettingsOut:
    settings = _get_or_create(db, ctx.store_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)
    db.flush()
    record_audit(db, action="ai_settings_updated", user_id=user.id, store_id=ctx.store_id, target_type="store_ai_settings", target_id=settings.id)
    db.commit()
    return StoreAISettingsOut.model_validate(settings)
