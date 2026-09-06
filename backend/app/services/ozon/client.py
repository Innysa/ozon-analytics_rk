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

Also present, but deliberately NOT relied on for anything yet:
  POST /v1/analytics/product-queries          - see get_product_queries()
  POST /v1/analytics/product-queries/details  - see get_product_query_details()
These two exist (confirmed via a dev.ozon.ru changelog entry: the methods
left beta on 2025-07-23) and are the Seller API equivalent of "Аналитика →
Товары в поиске → Запросы моего товара" — the same report this app already
imports from a real exported XLSX (app.services.search_query_import). But
their exact request/response field names could not be confirmed: both
docs.ozon.ru AND api-seller.ozon.ru itself are blocked by this sandbox's
network egress policy (confirmed via a direct curl attempt returning
`connect_rejected`, not merely assumed), so there was no way to inspect a
real response before writing these two methods. They return the raw parsed
JSON dict rather than a validated schema, and no parser/DB-writing code has
been built on top of them — see each method's own docstring.

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
        """Same endpoint as get_products_info(), keyed by offer_id instead
        of product_id. Used as a fallback for products whose product_id-keyed
        response comes back with sku == 0 — confirmed on a real store: Ozon
        can report sku == 0 for a product that hasn't yet had stock arrive at
        an Ozon warehouse, even though the product already has a real SKU
        visible in the seller's own "Аналитика" section; re-querying this
        same endpoint by offer_id for just that item was confirmed to return
        the real SKU instead of 0. Called only for the handful of items that
        actually need it (see app.api.routes.sync's product sync), not for
        every product, so it doesn't slow down a normal sync."""
        data = self._post("/v3/product/info/list", {"offer_id": offer_ids})
        return OzonProductInfoListResponse.model_validate(data)

    def get_product_queries(
        self,
        *,
        date_from: str,
        date_to: str,
        skus: list[str] | None = None,
        limit: int = 100,
        page_token: str = "",
    ) -> dict:
        """POST /v1/analytics/product-queries — per-SKU search-query
        analytics (general list: which queries led to this product).

        UNCONFIRMED CONTRACT — see this module's docstring for why. The
        request body below is a best-effort guess based on this app's other
        Ozon Seller/Performance analytics calls, NOT verified documentation:
          - date_from/date_to as "YYYY-MM-DD": this app already hit one real
            case (the Performance API statistics-report endpoint) where the
            obvious DD.MM.YYYY guess was rejected and ISO was required —
            treat this as the more likely format, but unconfirmed here.
          - skus, limit, page_token: named by analogy with this same
            client's other paginated calls; Ozon may use different names
            (e.g. "product_id" instead of "skus", as /v3/product/info/list
            does) or a different pagination style entirely (last_id vs
            page_token vs offset).
        Returns the raw parsed JSON dict, not a validated schema — do not
        write a parser or persist this response until a real call's exact
        shape has been confirmed (see app.services.search_query_import for
        the confirmed, real-file-based path this app relies on today)."""
        body: dict = {"date_from": date_from, "date_to": date_to, "limit": limit}
        if skus:
            body["skus"] = skus
        if page_token:
            body["page_token"] = page_token
        return self._post("/v1/analytics/product-queries", body)

    def get_product_query_details(
        self,
        *,
        query: str,
        date_from: str,
        date_to: str,
        skus: list[str] | None = None,
        limit: int = 100,
        page_token: str = "",
    ) -> dict:
        """POST /v1/analytics/product-queries/details — detail for one
        specific query (the method described as the equivalent of "Запросы
        моего товара", i.e. the one that should carry product position).

        Same UNCONFIRMED CONTRACT caveat as get_product_queries() above —
        additionally, it's not confirmed whether the query-text parameter
        is named "query", "queries" (plural/list), or "search_query"."""
        body: dict = {"query": query, "date_from": date_from, "date_to": date_to, "limit": limit}
        if skus:
            body["skus"] = skus
        if page_token:
            body["page_token"] = page_token
        return self._post("/v1/analytics/product-queries/details", body)
