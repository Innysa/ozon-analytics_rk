class OzonAPIError(Exception):
    """Base error for anything that goes wrong talking to Ozon Seller API."""


class OzonAuthError(OzonAPIError):
    """Client-Id / Api-Key rejected by Ozon."""


class OzonFeatureUnavailable(OzonAPIError):
    """The store's Ozon plan does not include this method (e.g. reviews
    endpoints require a Premium Plus subscription). The caller must degrade
    gracefully — offer CSV/XLSX import and manual-copy replies instead of
    breaking the page."""


class OzonRateLimited(OzonAPIError):
    """Too many requests — caller should back off and retry.

    retry_after_s carries Ozon's own `Retry-After` response header when
    present (confirmed real on FBO postings, 2026-09-11 — a real account's
    /v2/posting/fbo/list started returning sustained 429s that outlasted
    this client's old fixed backoff schedule, dropping that store's whole
    day of FBO order data silently into a PARTIAL sync run each time). None
    when Ozon didn't send the header — the retry falls back to plain
    exponential backoff in that case (see OzonSellerClient._post())."""

    def __init__(self, message: str, *, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s
