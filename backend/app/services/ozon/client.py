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

Also present:
  POST /v1/analytics/data - see get_analytics_data()
"Данные аналитики", the Seller API equivalent of the "Аналитика → Графики"
report — NOT the same report as "Аналитика → Товары" (search_query_import/
product_analytics_import's CSV upload); metric names differ and the two have
not been confirmed to line up 1:1. The REQUEST contract below is CONFIRMED
straight from the official docs (a real screenshot of the Redoc page for
this method, read 2026-09-10 — this sandbox has no network access to
docs.ozon.ru either, so this came from the user, not a live fetch):

  - date_from/date_to: plain date strings (the docs' own example uses
    "2020-09-01", no time component — unlike product-queries above, which
    needs a full "...T00:00:00Z" timestamp; do not assume the same format).
    Without a Premium Plus/Premium Pro subscription, date_from must be
    within the last 3 months.
  - dimension (list[str], required): grouping. Free for every seller:
    "unknownDimension", "sku", "spu", "day", "week", "month". Premium
    Plus/Pro only: "year", "category1", "category2", "brand", "modelID",
    "descriptionType".
  - metrics (list[str], required, max 14 per call — a 15th raises
    InvalidArgument): free for every seller: "revenue", "ordered_units".
    Premium Plus/Pro only: "unknown_metric", "hits_view_search",
    "hits_view_pdp", "hits_view", "hits_tocart_search", "hits_tocart_pdp",
    "hits_tocart", "session_view_search", "session_view_pdp",
    "session_view", "conv_tocart_search", "conv_tocart_pdp", "conv_tocart",
    "returns", "cancellations", "delivered_units", "position_category".
  - filters (list[dict]), sort (list[dict]): shape not confirmed beyond
    "array of objects" — not used by get_analytics_data() below yet.
  - limit (int, required, 1-1000), offset (int).
  - Rate limit: at most 1 request per minute per the docs — any future sync
    built on this must respect that, not just retry-on-429.

The RESPONSE shape is now CONFIRMED too (backend/scripts/debug_analytics_data.py
against a real account with Premium Plus, 2026-09-10) — see
app.services.ozon.schemas's OzonAnalyticsDataResponse for the full shape and
the real example. In short: `result.data[]` is `{"dimensions": [...],
"metrics": [...]}`, where both arrays are POSITIONAL to the request's own
`dimension`/`metrics` lists — e.g. requesting dimension=["sku", "day"] gives
`dimensions[0] = {"id": "<sku>", "name": "<product title>"}` and
`dimensions[1] = {"id": "<YYYY-MM-DD>", "name": ""}`; `metrics[i]` is the
value for `metrics` request-list position i, not labelled by key at all.
get_analytics_data() therefore returns a validated OzonAnalyticsDataResponse
rather than a raw dict.

Also present:
  POST /v2/posting/fbo/list        - list_fbo_postings()
  POST /v3/posting/fbs/list        - list_fbs_postings()
Both CONFIRMED against a real account (backend/scripts/debug_orders_finance_
api.py) and in production use — see app.services.order_daily_sync_service
and OrderDailyStatistic's own docstring for the confirmed field-level
contract. This module's own "UNCONFIRMED" note used to cover these two as
well as list_finance_transactions() below — that was stale; only the
finance-transactions method still carries that caveat.

  POST /v3/finance/transaction/list - list_finance_transactions()
CONFIRMED OBSOLETE as of 2026-09-10: a real account got HTTP 400 `{"code":
9, "message":"obsolete method cannot be used"}` calling this exact method
via debug_orders_finance_api.py. Ozon apparently retired it — reporting
elsewhere (not independently verified against Ozon's own docs, which this
sandbox cannot reach) points to a July 2026 deprecation with the method
fully disabled by 2026-09-08, split into several `/v1/finance/accrual/*`
methods instead (exact paths/contract NOT confirmed — do not build on that
name without a real docs screenshot or a live account's own docs access
confirming it, same discipline as everything else in this module). This is
the blocker for Логистика/Хранение/Штрафы/Прочие удержания on the
Дашборд — see README's "Архитектурно подготовлено" section. Do NOT call
this method — it will always fail now. list_finance_transactions() and its
schemas are left in place only as a record of the confirmed-dead contract,
not for reuse.

Every request carries the target store's own Client-Id / Api-Key headers —
callers must never share credentials across stores.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.services.ozon.exceptions import (
    OzonAPIError,
    OzonAuthError,
    OzonFeatureUnavailable,
    OzonRateLimited,
)
from app.services.ozon.schemas import (
    OzonAnalyticsDataResponse,
    OzonFinanceTransactionListResponse,
    OzonPostingListResponse,
    OzonProductInfoListResponse,
    OzonProductListResponse,
    OzonReviewCommentItem,
    OzonReviewListResponse,
)

BASE_URL = "https://api-seller.ozon.ru"

logger = logging.getLogger(__name__)

_FALLBACK_RATE_LIMIT_WAIT = wait_exponential(multiplier=1, min=1, max=30)


def _parse_retry_after(header_value: str | None) -> float | None:
    """Ozon's `Retry-After` on a 429, when present, per HTTP convention is
    either a whole number of seconds or an HTTP-date. Only the
    seconds form has been observed in practice; the HTTP-date form isn't
    parsed (no confirmed real example to build against) — a header in that
    form is treated the same as no header, falling back to plain
    exponential backoff rather than guessing a parse."""
    if not header_value:
        return None
    try:
        return float(header_value)
    except ValueError:
        return None


def _wait_for_ozon_rate_limit(retry_state):
    """tenacity `wait` callable: honors Ozon's own `Retry-After` seconds
    when the failing attempt's OzonRateLimited carried one, otherwise falls
    back to exponential backoff — see _post()'s own retry decorator for why
    this exists."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, OzonRateLimited) and exc.retry_after_s is not None:
        return max(exc.retry_after_s, 0.0)
    return _FALLBACK_RATE_LIMIT_WAIT(retry_state)


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
        # 8 attempts -> 7 waits, honoring Ozon's own `Retry-After` header
        # when it sends one (see _wait_for_ozon_rate_limit below), falling
        # back to exponential backoff (1, 2, 4, 8, 16, 30, 30s — capped at
        # max=30, ~121s worst case) when it doesn't.
        #
        # CONFIRMED STILL NOT ENOUGH for a real account's /v2/posting/fbo/
        # list (2026-09-11): even after this exact 8-attempt/Retry-After
        # budget was deployed, a real manual "Обновить заказы (авто)" run
        # exhausted it and still got "FBO: Ozon вернул 429 Too Many
        # Requests" (FBS on the same account, same run, succeeded — 547
        # fetched, all FBS). That rules out "our own retry just isn't
        # patient enough within one request" as the whole story — either
        # this account's real limit for this specific (heavy —
        # `with: financial_data`, previously also `analytics_data`, now
        # dropped below since this app never reads it) endpoint is a
        # sustained/longer-window block that no single-request retry budget
        # can bridge, or the account had an hour/day-level quota already
        # used up. NOT YET DISTINGUISHED — no confirmed Ozon documentation
        # of the real limit for this method, and this sandbox can't reach
        # api-seller.ozon.ru to test directly.
        #
        # Ozon support's own answer (2026-09-11, in response to a support
        # ticket asking exactly this): the account-wide cap is 50 req/s per
        # Client-Id, "also method-specific limits apply" (no number given
        # for this method). This RULES OUT the account-wide cap as the
        # cause — this client's own throttle (_min_interval_s=0.25s, i.e.
        # 4 req/s) plus the chunk-to-chunk pause below are already far
        # under 50/s, yet FBO still hit sustained 429s. So the real
        # bottleneck is a stricter, undisclosed per-method quota on this
        # specific (heavy, `with: financial_data`) endpoint — the
        # chunking/adaptive-pause/retry strategy already in place is the
        # right mitigation for that (there's no fixed number to size it
        # against), not something to relax based on the 50/s figure.
        # before_sleep_log below logs
        # every retry attempt (level, attempt count, wait chosen) so the
        # NEXT real occurrence is observable in the app's own logs instead
        # of needing another bespoke diagnostic run.
        #
        # CONFIRMED 2026-09-12: a sustained-429 chunk's logs showed all 7
        # waits at a flat 1.0s, no escalation whatsoever — reproduced this
        # exact decorator locally against a function that always raises
        # OzonRateLimited with no retry_after_s, and it escalated correctly
        # (1,2,4,8,16,30,30), ruling out a bug in _wait_for_ozon_rate_limit/
        # wait_exponential themselves. The far more likely explanation: Ozon
        # was sending a real `Retry-After` header (probably "1") on every
        # single attempt, honored verbatim each time per this decorator's
        # own design — but before_sleep_log's line only ever showed the
        # CHOSEN wait, never the raw header value, so there was no way to
        # tell "Ozon keeps saying 1s and it's genuinely not enough" apart
        # from "our fallback backoff is stuck". _post() below now logs the
        # raw Retry-After header (and includes it in OzonRateLimited's own
        # message) at the moment Ozon sends it, so this is directly
        # observable next time instead of needing a fresh reproduction.
        stop=stop_after_attempt(8),
        wait=_wait_for_ozon_rate_limit,
        retry=retry_if_exception_type(OzonRateLimited),
        before_sleep=before_sleep_log(logger, logging.WARNING),
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
            retry_after_raw = response.headers.get("Retry-After")
            retry_after_s = _parse_retry_after(retry_after_raw)
            # CONFIRMED 2026-09-12 (real account): a sustained-429 chunk logged
            # SEVEN retries in a row all waiting exactly 1.0s, no escalation at
            # all — which _wait_for_ozon_rate_limit only does when Ozon's OWN
            # `Retry-After` header carries a value (honored verbatim instead of
            # the exponential fallback, by design). before_sleep_log's line
            # only ever showed the CHOSEN wait, never the raw header itself, so
            # there was no way to tell "Ozon told us 1s, repeatedly, and it just
            # wasn't enough" apart from "our own backoff is stuck at 1s" (which
            # a direct reproduction of this exact decorator ruled out — it
            # escalates correctly in isolation). Logging the raw header here,
            # at the moment Ozon sends it, closes that gap for next time
            # without needing another investigation round.
            logger.warning(
                "Ozon 429 на %s: Retry-After=%r (распознано как %r секунд)", path, retry_after_raw, retry_after_s
            )
            raise OzonRateLimited(
                f"Ozon вернул 429 Too Many Requests на {path} (Retry-After={retry_after_raw!r} -> {retry_after_s!r}с)",
                retry_after_s=retry_after_s,
            )
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

    def get_analytics_data(
        self,
        *,
        date_from: str,
        date_to: str,
        dimension: list[str],
        metrics: list[str],
        limit: int = 1000,
        offset: int = 0,
        filters: list[dict] | None = None,
        sort: list[dict] | None = None,
    ) -> OzonAnalyticsDataResponse:
        """POST /v1/analytics/data — CONFIRMED request AND response contract,
        see this module's own docstring. date_from/date_to are plain dates
        ("YYYY-MM-DD"), NOT the "...T00:00:00Z" timestamp the product-queries
        methods need. `dimension`/`metrics` order here must match whatever
        the caller uses to index the response's positional dimensions/metrics
        arrays — see product_analytics_daily_sync_service for the fixed
        order this app actually uses."""
        body: dict = {
            "date_from": date_from,
            "date_to": date_to,
            "dimension": dimension,
            "metrics": metrics,
            "limit": limit,
            "offset": offset,
            "filters": filters or [],
        }
        if sort:
            body["sort"] = sort
        return OzonAnalyticsDataResponse.model_validate(self._post("/v1/analytics/data", body))

    def list_fbo_postings(
        self,
        *,
        date_from: str,
        date_to: str,
        offset: int = 0,
        limit: int = 1000,
    ) -> OzonPostingListResponse:
        """POST /v2/posting/fbo/list — orders fulfilled from Ozon's own
        warehouse (FBO). CONFIRMED against a real account and in production
        use (app.services.order_daily_sync_service) — see this module's own
        docstring and OrderDailyStatistic's docstring for the confirmed
        field-level contract. date_from/date_to per Ozon's public docs are
        full ISO-8601 timestamps, same convention as
        get_product_query_details().

        `with.analytics_data` is deliberately NOT requested (dropped
        2026-09-11) — nothing in this codebase reads `posting.
        analytics_data` (confirmed: only ever noted "for the future", never
        parsed), so asking Ozon to attach it was pure unused weight on an
        already-heavy call. Dropped as one concrete step after a real
        account's FBO sync started hitting sustained 429s that this
        client's retry budget couldn't ride out — see _post()'s own retry
        decorator comment for the full incident and what's still
        unconfirmed. `financial_data` stays — commission/old_price from it
        ARE used (see OrderDailyStatistic's docstring)."""
        body = {
            "dir": "ASC",
            "filter": {"since": date_from, "to": date_to},
            "offset": offset,
            "limit": limit,
            "with": {"financial_data": True},
        }
        data = self._post("/v2/posting/fbo/list", body)
        return OzonPostingListResponse.model_validate(data)

    def list_fbs_postings(
        self,
        *,
        date_from: str,
        date_to: str,
        offset: int = 0,
        limit: int = 1000,
    ) -> OzonPostingListResponse:
        """POST /v3/posting/fbs/list — orders fulfilled by the seller (FBS).
        Same CONFIRMED status as list_fbo_postings() — see there, including
        why `with.analytics_data` is deliberately not requested here
        either (dropped 2026-09-11, unused weight)."""
        body = {
            "dir": "ASC",
            "filter": {"since": date_from, "to": date_to},
            "offset": offset,
            "limit": limit,
            "with": {"financial_data": True},
        }
        data = self._post("/v3/posting/fbs/list", body)
        return OzonPostingListResponse.model_validate(data)

    def list_finance_transactions(
        self,
        *,
        date_from: str,
        date_to: str,
        page: int = 1,
        page_size: int = 1000,
    ) -> OzonFinanceTransactionListResponse:
        """POST /v3/finance/transaction/list — CONFIRMED OBSOLETE, DO NOT
        CALL. Ozon returns HTTP 400 `{"code": 9, "message": "obsolete method
        cannot be used"}` (confirmed 2026-09-10 against a real account via
        backend/scripts/debug_orders_finance_api.py) — see this module's own
        docstring for what's known so far about a replacement. Left in place
        only as a record of the confirmed-dead contract."""
        body = {
            "filter": {"date": {"from": date_from, "to": date_to}, "transaction_type": "all"},
            "page": page,
            "page_size": page_size,
        }
        data = self._post("/v3/finance/transaction/list", body)
        return OzonFinanceTransactionListResponse.model_validate(data)

    def probe_finance_endpoint(self, path: str, body: dict) -> dict:
        """DIAGNOSTIC ONLY — see backend/scripts/debug_cash_flow_statement.py.
        Generic raw POST for exploring a candidate replacement for the now-
        confirmed-obsolete /v3/finance/transaction/list (see
        list_finance_transactions()'s own docstring). A real account's own
        Ozon Seller API key confirmed these method NAMES exist (2026-09-10,
        via the account's own key-permissions listing) — the exact request/
        response CONTRACT for any of them is NOT confirmed, so this takes a
        caller-supplied body rather than a typed one, letting the diagnostic
        script try several plausible shapes in one run without formalizing a
        guess as if it were confirmed. Returns the raw parsed JSON (or raises
        the same OzonAPIError family every other method here does). Do NOT
        build a sync/scheduler/UI on top of a response captured this way
        without first confirming the shape is stable (e.g. a second real
        example, or the official docs)."""
        return self._post(path, body)

    def get_cash_flow_statement(
        self, *, date_from: str, date_to: str, page: int = 1, page_size: int = 1000, with_details: bool = True
    ) -> dict:
        """POST /v1/finance/cash-flow-statement/list — CONFIRMED (two rounds
        of real diagnostic output against a real account, 2026-09-10, via
        backend/scripts/debug_cash_flow_statement.py) as the replacement for
        the now-obsolete /v3/finance/transaction/list (see
        list_finance_transactions()'s own docstring). Full contract details
        — including what is deliberately NOT modeled (no per-category split
        inside "services") — are in
        app.models.cash_flow_statement_period.CashFlowStatementPeriod's own
        docstring; this method intentionally returns the raw parsed dict
        (not a typed schema) since only app.services.
        cash_flow_statement_sync_service reads specific fields out of it —
        the same "raw dict, caller picks fields" pattern as
        get_product_queries().

        date_from/date_to are full ISO-8601 timestamps — confirmed working
        with the same "...T00:00:00Z"/"...T23:59:59Z" convention already
        used for postings. The monthly form ({"date": {"year", "month"}})
        does NOT work here — Ozon requires date.from/date.to even though
        the report is inherently periodic; Ozon also does NOT split results
        by the requested range — it returns its OWN roughly weekly periods
        regardless (confirmed: 7-day windows stepping backward from
        date_to, not aligned to the caller's own dates)."""
        body = {
            "date": {"from": date_from, "to": date_to},
            "page": page,
            "page_size": page_size,
            "with_details": with_details,
        }
        return self._post("/v1/finance/cash-flow-statement/list", body)

    def get_rating_summary(self) -> dict:
        """POST /v1/rating/summary — CONFIRMED (2026-09-11, real account, via
        backend/scripts/debug_rating_summary.py) as the source for the
        store-wide «Локализация» % shown on the «РНП Товары» planner's
        "Итого" row. Empty body. See
        app.models.store_rating_summary.StoreRatingSummary's own docstring
        for the confirmed response shape (`localization_index.
        localization_percentage`/`.calculation_date`) and why this can only
        ever be a single account-wide number, never per-product. Returns the
        raw parsed dict — same "raw dict, caller picks fields" pattern as
        get_cash_flow_statement(), since only
        app.api.routes.sync's rating-summary sync route reads from it."""
        return self._post("/v1/rating/summary", {})
