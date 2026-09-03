"""Logging setup that never leaks secrets.

Any log line that might contain an API key, session token, or password must go
through `redact()` first. This is a best-effort guard, not a substitute for
keeping secrets out of log statements in the first place.
"""
from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = [
    re.compile(r"(Api-Key[:=]\s*)([^\s,\"']+)", re.IGNORECASE),
    re.compile(r"(Authorization[:=]\s*Api-Key\s+)([^\s,\"']+)", re.IGNORECASE),
]


def redact(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", redacted)
    return redacted


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
