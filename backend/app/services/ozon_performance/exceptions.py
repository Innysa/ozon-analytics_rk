class OzonPerformanceAPIError(Exception):
    """Base error for anything that goes wrong talking to Ozon Performance API."""


class OzonPerformanceAuthError(OzonPerformanceAPIError):
    """Client-Id / Client-Secret rejected, or token exchange failed."""


class OzonPerformanceRateLimited(OzonPerformanceAPIError):
    """Too many requests — caller should back off and retry."""
