"""Shared helpers for CSV/XLSX import services (reviews, advertising
statistics, product-card analytics): tolerant value parsing and the common
result shape. Kept here instead of duplicated per-importer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

MISSING_SENTINELS = {"", "-", "–", "—", "n/a", "na"}

# Python's \s already matches regular space, NBSP (U+00A0), narrow NBSP
# (U+202F), and thin space (U+2009) — all observed as thousands separators
# across different Ozon exports.
_RU_THOUSANDS_RE = re.compile(r"\s")


def clean_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in MISSING_SENTINELS:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def clean_int(value: object) -> int | None:
    n = clean_number(value)
    return int(n) if n is not None else None


def clean_ru_formatted_number(value: object) -> float | None:
    """Parses numbers rendered as pre-formatted Russian-locale text, e.g.
    '185 862', '72 650 ₽', '2,98%', '0%' — space-grouped thousands
    (plain space, NBSP, or narrow NBSP), comma decimal separator, optional
    trailing '₽' or '%'. Unlike clean_number (which assumes plain numeric
    cells with only a comma-for-dot swap), this is for exports — such as
    "Аналитика → Запросы" — that hand back every metric as display text."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and pd.isna(value):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in MISSING_SENTINELS:
        return None
    text = text.replace("₽", "").replace("%", "").strip()
    text = _RU_THOUSANDS_RE.sub("", text)
    text = text.replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def clean_ru_formatted_int(value: object) -> int | None:
    n = clean_ru_formatted_number(value)
    return int(n) if n is not None else None


def clean_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text in MISSING_SENTINELS:
        return None
    return text


@dataclass
class ImportResult:
    fetched: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    errors: list[str] = field(default_factory=list)
