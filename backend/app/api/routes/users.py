from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.security import hash_password
from app.db.session import get_db
from app.models.membership import StoreMembership
from app.models.store import Store
from app.models.user import User
from app.schemas.store import MembershipCreate, MembershipOut, StoreOut
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.audit import record_audit

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _admin: User = Depends(require_admin)) -> list[UserOut]:
    users = db.scalars(select(User).order_by(User.created_at)).all()
    return [UserOut.model_validate(u) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> UserOut:
    existing = db.scalars(select(User).where(User.email == payload.email.lower())).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Пользователь с таким email уже существует")
    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
    )
    db.add(user)
    db.flush()
    record_audit(db, action="user_created", user_id=admin.id, target_type="user", target_id=user.id)
    db.commit()
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, payload: UserUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> UserOut:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    db.flush()
    record_audit(db, action="user_updated", user_id=admin.id, target_type="user", target_id=user.id)
    db.commit()
    return UserOut.model_validate(user)


@router.get("/{user_id}/memberships", response_model=list[MembershipOut])
def list_user_memberships(user_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)) -> list[MembershipOut]:
    rows = db.scalars(select(StoreMembership).where(StoreMembership.user_id == user_id)).all()
    return [MembershipOut.model_validate(r) for r in rows]


@router.post("/memberships", response_model=MembershipOut, status_code=status.HTTP_201_CREATED)
def assign_membership(payload: MembershipCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> MembershipOut:
    target_user = db.get(User, payload.user_id)
    store = db.get(Store, payload.store_id)
    if not target_user or not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь или магазин не найден")

    existing = db.scalars(
        select(StoreMembership).where(
            StoreMembership.user_id == payload.user_id, StoreMembership.store_id == payload.store_id
        )
    ).first()
    if existing:
        existing.role = payload.role
        db.flush()
        record_audit(db, action="membership_updated", user_id=admin.id, target_type="membership", target_id=existing.id)
        db.commit()
        return MembershipOut.model_validate(existing)

    membership = StoreMembership(user_id=payload.user_id, store_id=payload.store_id, role=payload.role)
    db.add(membership)
    db.flush()
    record_audit(db, action="membership_created", user_id=admin.id, target_type="membership", target_id=membership.id)
    db.commit()
    return MembershipOut.model_validate(membership)


@router.delete("/memberships/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_membership(membership_id: str, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> None:
    membership = db.get(StoreMembership, membership_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Доступ не найден")
    db.delete(membership)
    record_audit(db, action="membership_revoked", user_id=admin.id, target_type="membership", target_id=membership_id)
    db.commit()
