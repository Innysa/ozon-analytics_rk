"""OzonSellerClient — the only place in the codebase that talks HTTP to Ozon.

Kept deliberately separate from routes/DB/UI logic: routes call this client
and translate its results/exceptions into API responses; this client never
imports SQLAlchemy models or touches the database.

Endpoints implemented here (Ozon Seller API, base https://api-seller.ozon.ru):
  POST /v1/review/list           - paginated list of reviews
  POST /v1/review/info           - single review detail
  POST /v1/review/comment/list   - comments/replies on a review
  POST /v1/review/comment/create - post a reply/comment to a review
  POST /v3/product/list          - paginated list of product ids/skus
  POST /v3/product/info/list     - product details (name, price, stocks) by id

These are documented as *beta* methods that require an Ozon Premium Plus
subscription (https://docs.ozon.ru/api/seller/). Field names were verified
against the current public documentation and community references at the
time this was written; if Ozon changes the contract, `OzonReviewItem`'s
`extra="allow"` config means unknown fields are preserved rather than
dropped, and unexpected shapes should be re-verified against the official
docs before being relied upon.

Also present:
  POST /v1/analytics/product-queries          - see get_product_queries()
  POST /v1/analytics/product-queries/details  - see get_product_query_details()
These are the Seller API equivalent of "Аналитика → Товары в поиске →
Запросы моего товара" — the same report this app used to require as a
manual XLSX upload (app.services.search_query_import) before this pair was
wired up. Their contract below is CONFIRMED against a real account via a
manual curl test (this sandbox's own network egress to api-seller.ozon.ru
is blocked, so the contract was verified externally, not by this process):

  - date_from/date_to: full ISO-8601 timestamp WITH time and a trailing
    "Z", e.g. "2026-08-08T00:00:00Z" — a bare "YYYY-MM-DD" date is rejected
    with "invalid google.protobuf.Timestamp value". Callers of this client
    must pass the full timestamp string themselves (see
    app.services.search_query_details_sync_service for the date_from
    00:00:00Z / date_to 23:59:59Z convention this app uses).
  - skus: list[str] of SKU ids. Confirmed working with 1 SKU in the request;
    the maximum SKUs accepted per call is NOT confirmed (untested on the
    real account) — see search_query_details_sync_service's own docstring
    for the conservative batch size this app uses until that limit is
    field-tested.
  - get_product_queries() ("общая аналитика по SKU за период"): takes
    `page_size` (NOT "limit"), range (0, 1000]. No `limit_by_sku`.
  - get_product_query_details() ("детализация по каждому запросу", the one
    that returns a `position` per individual query — what "Позиции в
    поиске" needs): requires BOTH `limit_by_sku` (range (0, 15]) and
    `page_size` (range (0, 100]) in the same request — Ozon returns a
    validation error if either is missing. There is no `query` request
    parameter (an earlier, unconfirmed version of this method guessed one
    and was wrong) — the response already carries one row per query,
    labelled by its own `query` field.
  - Whether only 1 request may be in flight per account at a time (as is
    the case for the unrelated Performance API statistics-report flow) is
    NOT confirmed for these two endpoints — see
    search_query_details_sync_service's docstring for why batches are
    still processed sequentially regardless.
Both methods return the raw parsed JSON dict rather than a validated
schema — the response shape (item field names, `total`/`page_count`) is
confirmed by the manual test, but not exhaustively enough to justify a
pydantic model yet. For get_product_query_details() specifically, a
verbatim raw response from a real account confirmed the per-row array is
under the top-level key "queries", NOT "items" — the response also always
includes a separate "items": [] field that is never the real data (see
app.services.search_query_details_sync_service._extract_query_rows, which
is what actually parses this). get_product_queries()'s own response shape
has not been separately re-verified against this finding.

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
from app.services.ozon.schemas import (
    OzonProductInfoListResponse,
    OzonProductListResponse,
    OzonReviewCommentItem,
    OzonReviewListResponse,
)

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
        # 5 attempts -> 4 waits of 1, 2, 4, 8s (wait_exponential caps at
        # max=8) before finally giving up — bumped up from 3 (only 2 waits,
        # ~3s total) after this was confirmed too impatient live: the
        # search-query-details sync's per-SKU fallback retry (see
        # app.services.search_query_details_sync_service) fires many
        # individual requests in a row when a grouped batch comes back
        # empty, and Ozon started rate-limiting mid-burst with 3 attempts'
        # worth of patience not being enough to ride it out.
        stop=stop_after_attempt(5),
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

    def list_products(self, *, last_id: str = "", limit: int = 1000) -> OzonProductListResponse:
        data = self._post(
            "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": limit},
        )
        return OzonProductListResponse.model_validate(data)

    def get_products_info(self, product_ids: list[int]) -> OzonProductInfoListResponse:
        data = self._post("/v3/product/info/list", {"product_id": product_ids})
        return OzonProductInfoListResponse.model_validate(data)

    def get_products_info_by_offer_id(self, offer_ids: list[str]) -> OzonProductInfoListResponse:
        """Same endpoint as get_products_info(), filtered by offer_id instead
        of product_id — /v3/product/info/list accepts either. Used as a
        fallback for products Ozon reports as sku=0 ("no SKU assigned yet")
        when queried by product_id: for a product that is "нет на складе"
        (not yet delivered to an Ozon warehouse) but already has a real SKU
        visible in Ozon's own "Аналитика" section, querying by product_id
        can still come back with sku=0, while the same product queried by
        offer_id returns the real, already-assigned SKU. See
        app.api.routes.sync's sync_ozon_products for where this is used."""
        data = self._post("/v3/product/info/list", {"offer_id": offer_ids})
        return OzonProductInfoListResponse.model_validate(data)

    def get_product_queries(
        self,
        *,
        date_from: str,
        date_to: str,
        skus: list[str] | None = None,
        page_size: int = 100,
    ) -> dict:
        """POST /v1/analytics/product-queries — per-SKU search-query
        analytics aggregated over the whole period (average position, no
        per-query breakdown). CONFIRMED contract — see this module's
        docstring. date_from/date_to must be full ISO timestamps
        ("...T00:00:00Z"); page_size range is (0, 1000]."""
        body: dict = {"date_from": date_from, "date_to": date_to, "page_size": page_size}
        if skus:
            body["skus"] = skus
        return self._post("/v1/analytics/product-queries", body)

    def get_product_query_details(
        self,
        *,
        date_from: str,
        date_to: str,
        skus: list[str] | None = None,
        limit_by_sku: int = 15,
        page_size: int = 100,
    ) -> dict:
        """POST /v1/analytics/product-queries/details — one row per
        individual search query per SKU, each carrying its own `position` —
        this is the method "Позиции в поиске" needs. CONFIRMED contract —
        see this module's docstring. date_from/date_to must be full ISO
        timestamps ("...T00:00:00Z"); BOTH limit_by_sku (range (0, 15]) and
        page_size (range (0, 100]) are required together, or Ozon returns a
        validation error."""
        body: dict = {
            "date_from": date_from,
            "date_to": date_to,
            "limit_by_sku": limit_by_sku,
            "page_size": page_size,
        }
        if skus:
            body["skus"] = skus
        return self._post("/v1/analytics/product-queries/details", body)
