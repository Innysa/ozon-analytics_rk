"""OzonSellerClient — the only place in the codebase that talks HTTP to Ozon.

Kept deliberately separate from routes/DB/UI logic: routes call this client
and translate its results/exceptions into API responses; this client never
imports SQLAlchemy models or touches the database.

Endpoints implemented here (Ozon Seller API, base https://api-seller.ozon.ru):
  POST /v1/review/list           - paginated list of reviews
  POST /v1/review/info           - single review detail
  POST /v1/review/comment/list   - comments/replies on a review
  POST /v1/review/comment/create - post a reply/comment to a review

These are documented as *beta* methods that require an Ozon Premium Plus
subscription (https://docs.ozon.ru/api/seller/). Field names were verified
against the current public documentation and community references at the
time this was written; if Ozon changes the contract, `OzonReviewItem`'s
`extra="allow"` config means unknown fields are preserved rather than
dropped, and unexpected shapes should be re-verified against the official
docs before being relied upon.

Every request carries the target store's own Client-Id / Api-Key headers —
callers must never share credentials across stores.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.ozon.exceptions import (
    OzonAPIError,
    OzonAuthError,
    OzonFeatureUnavailable,
    OzonRateLimited,
)
from app.services.ozon.schemas import OzonReviewCommentItem, OzonReviewListResponse

BASE_URL = "https://api-seller.ozon.ru"


@dataclass
class OzonCredentials:
    client_id: str
    api_key: str


class OzonSellerClient:
    def __init__(self, credentials: OzonCredentials, *, timeout: float = 15.0):
        self._credentials = credentials
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=timeout,
            headers={
                "Client-Id": credentials.client_id,
                "Api-Key": credentials.api_key,
                "Content-Type": "application/json",
            },
        )
        self._min_interval_s = 0.25  # simple client-side rate limit
        self._last_call_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OzonSellerClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type(OzonRateLimited),
    )
    def _post(self, path: str, json: dict) -> dict:
        self._throttle()
        try:
            response = self._client.post(path, json=json)
        except httpx.RequestError as exc:
            raise OzonAPIError(f"Сетевая ошибка при обращении к Ozon: {exc}") from exc
        finally:
            self._last_call_at = time.monotonic()

        if response.status_code == 401 or response.status_code == 403:
            raise OzonAuthError("Ozon отклонил Client-Id/Api-Key (401/403)")
        if response.status_code == 429:
            raise OzonRateLimited("Ozon вернул 429 Too Many Requests")
        if response.status_code == 404:
            # Beta/plan-gated methods can 404 for stores without the required subscription.
            raise OzonFeatureUnavailable(
                "Метод недоступен для этого магазина (может требоваться тариф Premium Plus)"
            )
        if response.status_code >= 500:
            raise OzonAPIError(f"Ozon вернул ошибку сервера {response.status_code}")
        if response.status_code >= 400:
            raise OzonAPIError(f"Ozon вернул ошибку {response.status_code}: {response.text[:500]}")

        return response.json()

    def check_connection(self) -> dict:
        """Lightweight connectivity/credentials check using review/list with the
        smallest limit Ozon actually accepts. Confirmed against a real deployment:
        Ozon rejects limit=1 with "Request validation error: invalid
        ReviewListRequest.Limit: value must be inside range [20, 100]" — the
        documented range starts at 20, not 1, despite this being a lightweight
        connectivity check that doesn't need any of the returned rows.
        Returns a dict describing outcome — never raises for expected failure modes,
        so the route layer can show a clear message instead of a stack trace."""
        try:
            data = self._post("/v1/review/list", {"limit": 20, "sort_dir": "ASC"})
            OzonReviewListResponse.model_validate(data)
            return {"ok": True, "reviews_api_available": True, "message": "Подключение к Ozon Seller API успешно"}
        except OzonAuthError:
            return {"ok": False, "reviews_api_available": None, "message": "Неверный Client-Id или Api-Key"}
        except OzonFeatureUnavailable:
            return {
                "ok": True,
                "reviews_api_available": False,
                "message": "Ключи приняты, но метод отзывов недоступен для этого магазина "
                "(вероятно, требуется тариф Ozon Premium Plus). Используйте загрузку CSV/XLSX.",
            }
        except OzonAPIError as exc:
            return {"ok": False, "reviews_api_available": None, "message": str(exc)}

    def list_reviews(self, *, status: str = "ALL", last_id: str = "", limit: int = 100) -> OzonReviewListResponse:
        data = self._post(
            "/v1/review/list",
            {"status": status, "last_id": last_id, "limit": limit, "sort_dir": "ASC"},
        )
        return OzonReviewListResponse.model_validate(data)

    def get_review_info(self, review_id: str) -> dict:
        return self._post("/v1/review/info", {"review_id": review_id})

    def list_comments(self, review_id: str, *, limit: int = 100) -> list[OzonReviewCommentItem]:
        data = self._post("/v1/review/comment/list", {"review_id": review_id, "limit": limit})
        return [OzonReviewCommentItem.model_validate(c) for c in data.get("comments", [])]

    def create_comment(self, review_id: str, text: str, *, mark_review_as_processed: bool = True) -> dict:
        return self._post(
            "/v1/review/comment/create",
            {
                "review_id": review_id,
                "text": text,
                "mark_review_as_processed": mark_review_as_processed,
            },
        )
