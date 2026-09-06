"""OzonPerformanceClient — the only place in the codebase that talks HTTP to
Ozon Performance API (advertising). Deliberately separate from
OzonSellerClient (app.services.ozon.client): different base host, different
auth flow (OAuth client_credentials vs. static Client-Id/Api-Key headers),
different product surface (advertising vs. reviews/orders/products).

Endpoints implemented here:
  POST /api/client/token               - OAuth client_credentials token exchange
  GET  /api/client/campaign            - list of advertising campaigns
  POST /api/client/statistics          - request an async daily-statistics report
  GET  /api/client/statistics/{UUID}   - poll report status
  GET  /api/client/statistics/report   - download the finished report (ZIP)

Base host: https://api-performance.ozon.ru (the older performance.ozon.ru
host was retired).

The statistics-report flow's exact contract (request/response shapes, real
error text, ZIP/CSV layout) was confirmed against a real account before this
was implemented — see app.services.advertising_daily_sync_service's module
docstring for the full write-up of every field-tested constraint (max 10
campaign IDs per request, max 1 report in flight per account, CSV column
order, encoding, etc.). Orchestrating a full sync (batching campaigns,
polling, retrying on "report busy"/token-expiry, parsing the ZIP, and
writing rows) is deliberately NOT this client's job — this class only wraps
the raw HTTP calls, exactly like OzonSellerClient does for the Seller API.

Every request carries the target store's own Performance Client-Id / Client-
Secret — callers must never share credentials across stores.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.ozon_performance.exceptions import (
    OzonPerformanceAPIError,
    OzonPerformanceAuthError,
    OzonPerformanceRateLimited,
    OzonPerformanceReportBusy,
)
from app.services.ozon_performance.schemas import (
    OzonCampaignItem,
    OzonCampaignListResponse,
    OzonStatisticsReportCreateResponse,
    OzonStatisticsReportStatus,
    OzonTokenResponse,
)

# Substring Ozon returns (inside a 400/409 error body) when a store already
# has a statistics report in progress — confirmed real error text, see
# OzonPerformanceReportBusy's docstring.
_REPORT_BUSY_MARKER = "Превышен лимит активных запросов"

BASE_URL = "https://api-performance.ozon.ru"

# The token is valid for `expires_in` seconds (documented as 1800s / 30 min);
# refresh a little early to avoid racing expiry mid-request.
_TOKEN_REFRESH_MARGIN_S = 60


@dataclass
class PerformanceCredentials:
    client_id: str
    client_secret: str


class OzonPerformanceClient:
    def __init__(self, credentials: PerformanceCredentials, *, timeout: float = 20.0):
        self._credentials = credentials
        self._client = httpx.Client(base_url=BASE_URL, timeout=timeout)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._min_interval_s = 0.3
        self._last_call_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OzonPerformanceClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)

    def _ensure_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        try:
            response = self._client.post(
                "/api/client/token",
                json={
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret,
                    "grant_type": "client_credentials",
                },
            )
        except httpx.RequestError as exc:
            raise OzonPerformanceAPIError(f"Сетевая ошибка при обращении к Ozon Performance API: {exc}") from exc

        if response.status_code in (401, 403):
            raise OzonPerformanceAuthError("Ozon Performance API отклонил Client-Id/Client-Secret")
        if response.status_code >= 400:
            raise OzonPerformanceAPIError(
                f"Ozon Performance API вернул ошибку {response.status_code} при получении токена: {response.text[:500]}"
            )

        token = OzonTokenResponse.model_validate(response.json())
        self._access_token = token.access_token
        expires_in = token.expires_in or 1800
        self._token_expires_at = time.monotonic() + max(expires_in - _TOKEN_REFRESH_MARGIN_S, 30)
        return self._access_token

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(OzonPerformanceRateLimited),
    )
    def _get(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        token = self._ensure_token()
        try:
            response = self._client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as exc:
            raise OzonPerformanceAPIError(f"Сетевая ошибка при обращении к Ozon Performance API: {exc}") from exc
        finally:
            self._last_call_at = time.monotonic()

        if response.status_code in (401, 403):
            self._access_token = None  # force re-auth on next call
            raise OzonPerformanceAuthError("Ozon Performance API отклонил токен доступа (401/403)")
        if response.status_code == 429:
            raise OzonPerformanceRateLimited("Ozon Performance API вернул 429 Too Many Requests")
        if response.status_code >= 500:
            raise OzonPerformanceAPIError(f"Ozon Performance API вернул ошибку сервера {response.status_code}")
        if response.status_code >= 400:
            raise OzonPerformanceAPIError(f"Ozon Performance API вернул ошибку {response.status_code}: {response.text[:500]}")

        return response.json()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(OzonPerformanceRateLimited),
    )
    def _post(self, path: str, json_body: dict) -> dict:
        self._throttle()
        token = self._ensure_token()
        try:
            response = self._client.post(path, json=json_body, headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as exc:
            raise OzonPerformanceAPIError(f"Сетевая ошибка при обращении к Ozon Performance API: {exc}") from exc
        finally:
            self._last_call_at = time.monotonic()

        if response.status_code in (401, 403):
            self._access_token = None  # force re-auth on next call
            raise OzonPerformanceAuthError("Ozon Performance API отклонил токен доступа (401/403)")
        if response.status_code == 429:
            raise OzonPerformanceRateLimited("Ozon Performance API вернул 429 Too Many Requests")
        if response.status_code >= 400 and _REPORT_BUSY_MARKER in response.text:
            raise OzonPerformanceReportBusy(response.text[:500])
        if response.status_code >= 500:
            raise OzonPerformanceAPIError(f"Ozon Performance API вернул ошибку сервера {response.status_code}")
        if response.status_code >= 400:
            raise OzonPerformanceAPIError(f"Ozon Performance API вернул ошибку {response.status_code}: {response.text[:500]}")

        return response.json()

    def _get_raw(self, path: str) -> bytes:
        """Like _get(), but for endpoints that return a binary body (the
        finished statistics report is a ZIP, not JSON)."""
        self._throttle()
        token = self._ensure_token()
        try:
            response = self._client.get(path, headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as exc:
            raise OzonPerformanceAPIError(f"Сетевая ошибка при обращении к Ozon Performance API: {exc}") from exc
        finally:
            self._last_call_at = time.monotonic()

        if response.status_code in (401, 403):
            self._access_token = None
            raise OzonPerformanceAuthError("Ozon Performance API отклонил токен доступа (401/403)")
        if response.status_code == 429:
            raise OzonPerformanceRateLimited("Ozon Performance API вернул 429 Too Many Requests")
        if response.status_code >= 400:
            raise OzonPerformanceAPIError(f"Ozon Performance API вернул ошибку {response.status_code}: {response.text[:500]}")

        return response.content

    def create_statistics_report(
        self,
        campaign_ids: list[str],
        date_from: str,
        date_to: str,
        *,
        group_by: str = "DATE",
    ) -> str:
        """Requests an async daily-statistics report for up to 10 campaigns
        (Ozon's own hard limit — enforced by the caller, see
        advertising_daily_sync_service). Returns the report UUID to poll."""
        data = self._post(
            "/api/client/statistics",
            {
                "campaigns": campaign_ids,
                "dateFrom": date_from,
                "dateTo": date_to,
                "groupBy": group_by,
            },
        )
        return OzonStatisticsReportCreateResponse.model_validate(data).UUID

    def get_statistics_report_status(self, uuid: str) -> OzonStatisticsReportStatus:
        data = self._get(f"/api/client/statistics/{uuid}")
        return OzonStatisticsReportStatus.model_validate(data)

    def download_statistics_report(self, link: str) -> bytes:
        """`link` is the path Ozon returns in the report-status response
        (e.g. "/api/client/statistics/report?UUID=..."), relative to this
        client's own base URL."""
        return self._get_raw(link)

    def check_connection(self) -> dict:
        """Exchanges a token and does one lightweight authenticated call.
        Never raises for expected failure modes, so the route layer can show
        a clear message instead of a stack trace."""
        try:
            self._ensure_token()
            data = self._get("/api/client/campaign")
            OzonCampaignListResponse.model_validate(data)
            return {"ok": True, "message": "Подключение к Ozon Performance API успешно"}
        except OzonPerformanceAuthError:
            return {"ok": False, "message": "Неверный Client-Id или Client-Secret для Ozon Performance API"}
        except OzonPerformanceAPIError as exc:
            return {"ok": False, "message": str(exc)}

    def list_campaigns(self) -> list[OzonCampaignItem]:
        data = self._get("/api/client/campaign")
        return OzonCampaignListResponse.model_validate(data).campaigns
