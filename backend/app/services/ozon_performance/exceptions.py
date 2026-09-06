class OzonPerformanceAPIError(Exception):
    """Base error for anything that goes wrong talking to Ozon Performance API."""


class OzonPerformanceAuthError(OzonPerformanceAPIError):
    """Client-Id / Client-Secret rejected, or token exchange failed."""


class OzonPerformanceRateLimited(OzonPerformanceAPIError):
    """Too many requests — caller should back off and retry."""


class OzonPerformanceReportBusy(OzonPerformanceAPIError):
    """Ozon Performance API allows only one statistics report in flight per
    account at a time; this is raised when Ozon rejects a new report request
    for that reason (confirmed real error text: "Превышен лимит активных
    запросов (максимум 1)"). The caller should wait and retry — usually
    means a previous run's report never finished polling (e.g. a crashed
    process), not that anything is wrong with this request."""


class OzonPerformanceReportTimeout(OzonPerformanceAPIError):
    """A statistics report stayed IN_PROGRESS past the configured poll
    timeout — Ozon never returned state == OK (or ERROR) in time."""


class OzonPerformanceReportFailed(OzonPerformanceAPIError):
    """Ozon itself reported the statistics report generation failed
    (state == ERROR)."""
