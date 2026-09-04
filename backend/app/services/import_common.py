"""Shared helpers for CSV/XLSX import services (reviews, advertising
statistics, product-card analytics): tolerant value parsing and the common
result shape. Kept here instead of duplicated per-importer."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

MISSING_SENTINELS = {"", "-", "–", "—", "n/a", "na"}


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


def clean_str(value: object) -> str | None:
    if value is None:
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
