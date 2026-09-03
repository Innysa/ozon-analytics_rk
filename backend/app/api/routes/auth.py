from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.security import create_session_token, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import CurrentUser, LoginRequest
from app.services.audit import record_audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=CurrentUser)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> CurrentUser:
    user = db.scalars(select(User).where(User.email == payload.email.lower())).first()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        record_audit(db, action="login", result="failure", message=f"failed login for {payload.email}")
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный email или пароль")

    token = create_session_token(user.id)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.ENV == "production",
        samesite="lax",
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    record_audit(db, action="login", user_id=user.id, result="success")
    db.commit()
    return CurrentUser.model_validate(user)


@router.post("/logout")
def logout(response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    record_audit(db, action="logout", user_id=user.id)
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user)) -> CurrentUser:
    return CurrentUser.model_validate(user)
