from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.change_history import ChangeHistory
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.user import User
from app.schemas.change_history import ChangeHistoryCreate, ChangeHistoryOut
from app.services.audit import record_audit

router = APIRouter(prefix="/api/stores/{store_id}/change-history", tags=["change-history"])


@router.get("", response_model=list[ChangeHistoryOut])
def list_change_history(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
) -> list[ChangeHistoryOut]:
    stmt = select(ChangeHistory).where(ChangeHistory.store_id == ctx.store_id)
    if product_id:
        stmt = stmt.where(ChangeHistory.product_id == product_id)
    rows = db.scalars(stmt.order_by(ChangeHistory.changed_at.desc())).all()

    user_ids = {r.user_id for r in rows if r.user_id}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return [
        ChangeHistoryOut(
            id=r.id, store_id=r.store_id, product_id=r.product_id, user_id=r.user_id,
            user_name=users[r.user_id].full_name if r.user_id in users else None,
            change_type=r.change_type, changed_at=r.changed_at, description=r.description, comment=r.comment,
        )
        for r in rows
    ]


@router.post("", response_model=ChangeHistoryOut, status_code=status.HTTP_201_CREATED)
def create_change_history_entry(
    payload: ChangeHistoryCreate,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChangeHistoryOut:
    product = db.get(Product, payload.product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    entry = ChangeHistory(
        store_id=ctx.store_id,
        product_id=payload.product_id,
        user_id=user.id,
        change_type=payload.change_type,
        changed_at=payload.changed_at,
        description=payload.description,
        comment=payload.comment,
    )
    db.add(entry)
    db.flush()
    record_audit(db, action="change_history_created", user_id=user.id, store_id=ctx.store_id, target_type="change_history", target_id=entry.id)
    db.commit()
    return ChangeHistoryOut(
        id=entry.id, store_id=entry.store_id, product_id=entry.product_id, user_id=entry.user_id,
        user_name=user.full_name, change_type=entry.change_type, changed_at=entry.changed_at,
        description=entry.description, comment=entry.comment,
    )
