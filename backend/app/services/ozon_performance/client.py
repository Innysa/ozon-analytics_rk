"""OzonPerformanceClient — the only place in the codebase that talks HTTP to
Ozon Performance API (advertising). Deliberately separate from
OzonSellerClient (app.services.ozon.client): different base host, different
auth flow (OAuth client_credentials vs. static Client-Id/Api-Key headers),
different product surface (advertising vs. reviews/orders/products).

Endpoints implemented here, verified against current public documentation
and community references at the time this was written:
  POST /api/client/token    - OAuth client_credentials token exchange
  GET  /api/client/campaign - list of advertising campaigns

Base host: https://api-performance.ozon.ru (the older performance.ozon.ru
host was retired). Day-by-day campaign statistics (spend/clicks/orders) use
an asynchronous "create report -> poll -> download" flow that was not
confirmed in enough field-level detail to implement without guessing at
request/response shapes, so it is intentionally NOT implemented here — see
app.models.future.AdvertisingDailyMetric. Before adding it, verify the exact
contract against https://docs.ozon.ru/api/performance/.

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
)
from app.services.ozon_performance.schemas import OzonCampaignItem, OzonCampaignListResponse, OzonTokenResponse

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
