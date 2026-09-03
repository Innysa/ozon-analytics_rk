"""Shared FastAPI dependencies: DB session, current user, and store-access
enforcement.

CRITICAL: every route that reads/writes store-scoped data MUST depend on
`require_store_access` (or `require_store_role`) rather than trusting a
store_id supplied by the client. This is what keeps store data from leaking
across tenants — see tests/test_store_isolation.py.
"""
from __future__ import annotations

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import read_session_token
from app.db.session import get_db
from app.models.membership import StoreMembership, StoreRole
from app.models.store import Store
from app.models.user import User

settings = get_settings()


def get_current_user(
    db: Session = Depends(get_db),
    session_token: str | None = Cookie(default=None, alias=settings.SESSION_COOKIE_NAME),
) -> User:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Не выполнен вход")
    user_id = read_session_token(session_token)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия недействительна или истекла")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден или деактивирован")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права администратора")
    return user


def get_membership(db: Session, user: User, store_id: str) -> StoreMembership | None:
    stmt = select(StoreMembership).where(
        StoreMembership.user_id == user.id, StoreMembership.store_id == store_id
    )
    return db.scalars(stmt).first()


_ROLE_RANK = {StoreRole.VIEWER: 0, StoreRole.MANAGER: 1, StoreRole.OWNER: 2}


class StoreContext:
    """Resolved, access-checked (store, role-rank) pair for the current request."""

    def __init__(self, store: Store, role: str):
        self.store = store
        self.role = role  # "admin" or a StoreRole value

    @property
    def store_id(self) -> str:
        return self.store.id


def require_store_access(
    store_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StoreContext:
    """Resolve store_id against the DB and verify the *authenticated user*
    actually has access to it — never trust the frontend's claim alone."""
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Магазин не найден")

    if user.is_admin:
        return StoreContext(store, role="admin")

    membership = get_membership(db, user, store_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Нет доступа к этому магазину")
    return StoreContext(store, role=membership.role.value)


def require_store_role(min_role: StoreRole):
    """Dependency factory: require at least `min_role` on the resolved store."""

    def _dep(ctx: StoreContext = Depends(require_store_access)) -> StoreContext:
        if ctx.role == "admin":
            return ctx
        if _ROLE_RANK.get(StoreRole(ctx.role), -1) < _ROLE_RANK[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Недостаточно прав (требуется роль не ниже '{min_role.value}')",
            )
        return ctx

    return _dep
