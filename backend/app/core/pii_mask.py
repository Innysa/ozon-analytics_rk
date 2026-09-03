"""Mask personal data before sending review text to an external AI provider.

Covers phone numbers, emails, and Ozon order numbers. This is a best-effort
regex-based mask, applied to the raw review text/pros/cons prior to any AI
Provider call — never send unmasked PII to a third-party API.
"""
from __future__ import annotations

import re

_PHONE_RE = re.compile(r"(\+?\d[\d\-\s\(\)]{8,}\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ORDER_RE = re.compile(r"\b\d{9,}-\d{3,}\b")  # Ozon-style order numbers, e.g. 12345678-0001


def mask_pii(text: str | None) -> str | None:
    if not text:
        return text
    masked = _EMAIL_RE.sub("[email скрыт]", text)
    masked = _ORDER_RE.sub("[номер заказа скрыт]", masked)
    masked = _PHONE_RE.sub("[телефон скрыт]", masked)
    return masked
