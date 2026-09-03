"""Password hashing and signed session-cookie helpers.

Sessions are opaque, signed tokens stored in an HttpOnly cookie — never in
localStorage and never returned in a JSON body. We use itsdangerous for
tamper-proof signing with an expiry baked in, keyed off SESSION_SECRET.
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import get_settings

_hasher = PasswordHasher()


def hash_password(plain_password: str) -> str:
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain_password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="oaa-session")


def create_session_token(user_id: str) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> str | None:
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")
