"""Symmetric encryption for secrets-at-rest (Ozon Client-Id / Api-Key).

Uses Fernet (AES-128-CBC + HMAC) keyed by APP_ENCRYPTION_KEY. The key must be a
urlsafe-base64 32-byte key, generated once and stored in Replit Secrets — never
committed to git. Encrypted values only ever live in the database; they are
never returned to the frontend and never written to logs.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class EncryptionNotConfigured(RuntimeError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.APP_ENCRYPTION_KEY:
        raise EncryptionNotConfigured(
            "APP_ENCRYPTION_KEY is not set. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and store it in Replit Secrets."
        )
    return Fernet(settings.APP_ENCRYPTION_KEY.encode())


def encrypt_secret(plain_text: str) -> str:
    return _fernet().encrypt(plain_text.encode()).decode()


def decrypt_secret(cipher_text: str) -> str:
    try:
        return _fernet().decrypt(cipher_text.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Не удалось расшифровать секрет — неверный APP_ENCRYPTION_KEY") from exc


def mask_secret(plain_text: str, visible: int = 4) -> str:
    """Return a masked representation safe to show in the UI (never the full key)."""
    if not plain_text:
        return ""
    if len(plain_text) <= visible:
        return "*" * len(plain_text)
    return f"{'*' * (len(plain_text) - visible)}{plain_text[-visible:]}"
