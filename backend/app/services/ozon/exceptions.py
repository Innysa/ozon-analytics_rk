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
    """Too many requests — caller should back off and retry."""
