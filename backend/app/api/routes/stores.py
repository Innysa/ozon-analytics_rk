from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.membership import StoreMembership
from app.models.store import Store
from app.models.sync_run import SyncRun, SyncStatus
from app.models.user import User
from app.schemas.store import StoreCreate, StoreOut
from app.services.audit import record_audit

router = APIRouter(prefix="/api/stores", tags=["stores"])


def _last_sync_at(db: Session, store_id: str):
    stmt = (
        select(func.max(SyncRun.finished_at))
        .where(SyncRun.store_id == store_id, SyncRun.status.in_([SyncStatus.SUCCESS, SyncStatus.PARTIAL]))
    )
    return db.scalar(stmt)


@router.get("", response_model=list[StoreOut])
def list_stores(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[StoreOut]:
    """Returns only the stores the current user may see: all of them for a
    platform admin, or only those with an explicit membership otherwise."""
    if user.is_admin:
        stores = db.scalars(select(Store).order_by(Store.created_at)).all()
        return [
            StoreOut(id=s.id, name=s.name, legal_name=s.legal_name, my_role="admin", last_sync_at=_last_sync_at(db, s.id))
            for s in stores
        ]

    rows = db.execute(
        select(Store, StoreMembership.role)
        .join(StoreMembership, StoreMembership.store_id == Store.id)
        .where(StoreMembership.user_id == user.id)
        .order_by(Store.created_at)
    ).all()
    return [
        StoreOut(id=s.id, name=s.name, legal_name=s.legal_name, my_role=role.value, last_sync_at=_last_sync_at(db, s.id))
        for s, role in rows
    ]


@router.post("", response_model=StoreOut, status_code=status.HTTP_201_CREATED)
def create_store(payload: StoreCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> StoreOut:
    store = Store(name=payload.name, legal_name=payload.legal_name)
    db.add(store)
    db.flush()
    record_audit(db, action="store_created", user_id=admin.id, store_id=store.id, target_type="store", target_id=store.id)
    db.commit()
    return StoreOut(id=store.id, name=store.name, legal_name=store.legal_name, my_role="admin", last_sync_at=None)
