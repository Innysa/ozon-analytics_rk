"""Orchestrates the automatic search-query-details sync from Ozon Seller
API's POST /v1/analytics/product-queries/details — replaces the manual
"Аналитика → Запросы" XLSX upload (app.services.search_query_import) with
an automatic pull, following the same architecture as the advertising
daily-statistics auto-sync (app.services.advertising_daily_sync_service):
batching, sequential processing, per-batch commit, upsert-by-key history.

Confirmed request/response contract (field-tested on a real account — see
app.services.ozon.client's own module docstring for the full write-up):

  1. date_from/date_to must be a full ISO-8601 timestamp with "Z"
     ("2026-08-08T00:00:00Z"), not a bare date — Ozon rejects the latter
     with "invalid google.protobuf.Timestamp value". This module always
     sends date_from at 00:00:00Z and date_to at 23:59:59Z for the resolved
     day range.

  1b. date_to cannot be "today" — confirmed live: a real run with
      date_to=today failed on every batch with the same error
      ("ProductQueriesDetails error: ... getPremiumAnalyticsPeriod rpc
      error: code = InvalidArgument desc = There is no data for the
      specified period"), while the same account's own confirmed working
      curl test used a date_to 2 days before the day it was run. Ozon's
      analytics aggregation evidently lags "today" by at least a day or two.
      This module therefore never defaults date_to to today — see
      settings.SEARCH_QUERY_STATS_DATA_LAG_DAYS.

  1c. Ozon ALSO rejects a date range it considers too long/too far in the
      past — with the exact SAME error text as 1b — confirmed live: a
      30-day window ending 2 days ago succeeded (HTTP 200, just happened to
      have no rows for the account tested), but an explicit 92-day window
      ending 7 days ago (2026-06-01..2026-08-31, requested to debug why the
      automatic sync kept reporting fetched=created=updated=0) was rejected
      outright as an InvalidArgument, not just returned empty.

      CONFIRMED (Ozon Seller's own in-app "База знаний" help widget, on the
      "Поисковые запросы" analytics page — this app's own network can't
      reach docs.ozon.ru/dev.ozon.ru/seller-edu.ozon.ru at all, so this came
      from a screenshot, not a fetch): with Premium Plus, the seller can
      "Выбрать период для показа статистики — последние 28 дней или
      календарный месяц в течение последнего года". settings.
      SEARCH_QUERY_STATS_DEFAULT_LOOKBACK_DAYS is set to this confirmed
      28-day figure. A specific past calendar month is a separately allowed
      period *shape* this module doesn't request (it only ever asks for a
      rolling N-day window ending near "today").

      Not fully closed, though: the UI's documented preset and the API's
      own enforced boundary need not be identical (30 days apparently still
      fit above), and an account without Premium Plus may have a smaller,
      undocumented allowance. As a defensive fallback for both cases, this
      module ALSO discovers the actual accepted window empirically per run
      when the confirmed default still gets rejected (see
      _fetch_with_period_shrink): it keeps date_to fixed and halves the span
      until Ozon accepts it or settings.SEARCH_QUERY_STATS_MIN_PERIOD_DAYS is
      reached, and records in SyncOutcome.errors whichever spans were
      rejected along the way so this is visible to the user, not silent.

  2. Both `limit_by_sku` (max 15) and `page_size` (max 100) are required
     together. This module always sends Ozon's own confirmed maxima
     (settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU /
     SEARCH_QUERY_STATS_PAGE_SIZE) to pull as much data per request as
     Ozon allows.

  3. The maximum number of SKUs accepted in one `skus` array is NOT
     confirmed (the real test only tried 1 SKU). SKUs are therefore
     processed in conservative batches of
     settings.SEARCH_QUERY_STATS_SKU_BATCH_SIZE — sized so that even the
     worst case (every SKU in the batch maxing out at LIMIT_BY_SKU query
     rows) still fits inside one PAGE_SIZE response, since this module does
     not implement pagination (no page/page_token field is confirmed to
     exist). If a response's `page_count` ever comes back > 1 despite that
     sizing, this is surfaced as a diagnostic in SyncOutcome.errors rather
     than silently dropping the truncated rows — the batch size should be
     lowered via settings if that is ever seen in practice.

  3b. CONFIRMED LIVE this assumption was actually WRONG (or at least
      unreliable): a debug request for exactly ONE sku (2953864771, real
      data confirmed independently via a manual XLSX export from Ozon's own
      cabinet — 769185 impressions etc.) returned 15 real rows, HTTP 200 —
      but the automatic sync's normal grouped batch (the same sku alongside
      several others, same date range) came back with items: [] for every
      batch, silently losing real data with no error at all. Rather than
      just shrink SEARCH_QUERY_STATS_SKU_BATCH_SIZE and hope, this module
      self-corrects: any batch of >1 SKUs that comes back with items: [] is
      automatically retried one SKU at a time (see
      _retry_empty_batch_per_sku) before being accepted as genuinely empty —
      this recovers real data regardless of whether Ozon's real per-request
      SKU limit is 1, or the grouped response is just unreliable for some
      other undocumented reason.

  3c. CONFIRMED LIVE as a direct consequence of 3b: firing many individual
      per-SKU requests in a row (whenever grouped batches keep coming back
      empty) can trigger Ozon's rate limiting (429 Too Many Requests)
      mid-burst. app.services.ozon.client.OzonSellerClient._post() already
      retries any 429 with exponential backoff (bumped from 3 to 5 attempts
      — 4 waits of 1, 2, 4, 8s — after 3 was confirmed too impatient for
      this specific burst pattern), so a transient 429 is invisible to this
      module in the common case. If Ozon is STILL rate-limiting after all of
      that, _retry_empty_batch_per_sku reports that specific SKU as
      uncollected for THIS run with a distinct message (rate-limited, not
      "no data") rather than folding it into a generic error — a plain
      re-run later should recover it.

  4. Whether Ozon allows only 1 request in flight per account at a time (as
     is confirmed for the unrelated Performance API statistics-report flow)
     is NOT confirmed for this endpoint. Batches are still processed
     strictly sequentially regardless — this is a plain synchronous REST
     call (unlike the async report flow), so sequential processing costs
     nothing extra and is the safe default until/unless concurrent batches
     are confirmed safe on a real account.

Field mapping from the API's response items onto SearchQueryStatistic is
NOT a confirmed one-to-one match with the manual XLSX importer's columns —
the API simply does not expose the same set of metrics:
  - unique_search_users -> people_searched
  - unique_view_users   -> people_saw
  - position            -> position_ozon
  - order_count         -> ordered_units_by_query (assumption: an "order"
    from the API is being treated as one ordered unit, same as the XLSX
    import's own field; Ozon's docs do not clarify whether an order can
    contain >1 unit of this SKU)
  - gmv                 -> ordered_sum_by_query_rub (currency is asserted
    to be "RUB" like every other monetary field in this app; a non-RUB
    response is surfaced as a diagnostic rather than silently mis-recorded)
  - view_conversion     -> conv_search_to_card_pct_ozon (best-guess mapping
    only — the API has a single conversion percentage, not the XLSX
    import's two (search→card, search→order); "view_conversion" reads
    closest to the search→card metric, but this is NOT confirmed against a
    documented formula. conv_search_to_order_pct_ozon is left NULL for
    API-sourced rows rather than guessed.)
  - query_index         -> query_index (new column; per Ozon's field name,
    this is the query's rank by importance/traffic for the SKU, NOT its
    search-results position)

No date_from/date_to per-row breakdown exists in the response (same as the
XLSX report) — one snapshot row per (SKU, query) is written per sync run,
keyed by (store_id, ozon_sku, query_text, period_start, period_end) exactly
like the manual importer (see SearchQueryStatistic's unique constraint).
Because the resolved date range slides forward with "today" on every run,
a daily scheduled run naturally produces a new period_end each day —
building history without overwriting older snapshots, which is exactly
what "Позиции в поиске" (search_query_analytics_service.compute_search_
query_positions) compares against.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.product import Product
from app.models.search_query_statistic import SearchQueryStatistic
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError, OzonRateLimited
from app.services.product_merge import merge_duplicate_products, pick_survivor

logger = logging.getLogger(__name__)

_SOURCE = "ozon_seller_api"

# Same cap as app.api.routes.sync's own product/info/list batching for the
# identical endpoint — /v3/product/info/list caps ids per request.
_PRODUCT_INFO_BATCH_SIZE = 100


@dataclass
class SyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _resolve_zero_sku_products(
    db: Session, store_id: str, client: OzonSellerClient, invalid_products: list[Product]
) -> tuple[list[Product], list[Product]]:
    """Same fallback as app.api.routes.sync's sync_ozon_products: a product
    queried by product_id can come back with sku=0 ("no SKU assigned yet")
    even though a real SKU is already assigned (confirmed live: offer_id
    "мус/вед/бел1/3" -> sku 5716615794) — /v3/product/info/list queried by
    offer_id instead resolves it. This module doesn't call Ozon at all for
    the product list (it reads Product rows straight from the DB), so a
    catalog row saved with sku=0 before that fix was applied — or simply not
    yet re-synced since Ozon assigned it a real sku — would otherwise be
    skipped by this sync forever. Only the zero-sku subset is retried, so a
    normal run with a clean catalog isn't slowed down by it.

    A resolved sku can collide with a SEPARATE Product row that already
    holds it (the historic sku=0 duplicate-row bug — see
    app.services.product_merge's module docstring) — writing it onto the
    placeholder unchanged would violate uq_product_store_sku, exactly the
    IntegrityError seen live for this same offer_id. Detected and merged via
    product_merge before the write, same as sync_ozon_products.

    Returns (resolved, still_invalid). Each resolved Product (either the
    corrected placeholder, or the pre-existing row it was merged into) has
    the confirmed real ozon_sku set in place (not yet flushed/committed —
    the caller owns that) so the corrected catalog also benefits, not just
    this one sync."""
    retryable = [p for p in invalid_products if p.offer_id]
    still_invalid = [p for p in invalid_products if not p.offer_id]
    if not retryable:
        return [], invalid_products

    resolved_by_offer_id: dict[str, str] = {}
    offer_ids = [p.offer_id for p in retryable]
    for batch_start in range(0, len(offer_ids), _PRODUCT_INFO_BATCH_SIZE):
        batch = offer_ids[batch_start : batch_start + _PRODUCT_INFO_BATCH_SIZE]
        try:
            info = client.get_products_info_by_offer_id(batch)
        except OzonAPIError:
            continue  # leave this batch's products in still_invalid below
        for item in info.items:
            if item.offer_id and item.sku:
                resolved_by_offer_id[item.offer_id] = str(item.sku)

    resolved: list[Product] = []
    for product in retryable:
        real_sku = resolved_by_offer_id.get(product.offer_id)
        if not real_sku:
            still_invalid.append(product)
            continue

        conflicting = (
            db.query(Product)
            .filter(Product.store_id == store_id, Product.ozon_sku == real_sku, Product.id != product.id)
            .first()
        )
        survivor = product
        if conflicting:
            keep, remove = pick_survivor(db, product, conflicting)
            merge_duplicate_products(db, keep=keep, remove=remove)
            survivor = keep
        survivor.ozon_sku = real_sku
        resolved.append(survivor)
    return resolved, still_invalid


def _is_period_rejected_error(exc: OzonAPIError) -> bool:
    """Ozon's specific InvalidArgument rejection of the requested date range
    itself (gRPC code 3, from the internal getPremiumAnalyticsPeriod RPC) —
    confirmed live with the EXACT SAME message text for two different root
    causes: date_to="today" (aggregation lag not satisfied — see
    SEARCH_QUERY_STATS_DATA_LAG_DAYS) and a date range Ozon considers too
    long/too far in the past (a 92-day window ending 7 days ago was
    rejected outright; a 30-day window ending 2 days ago was accepted). This
    is a validation rejection of the window itself, before any per-row data
    is even considered — a genuinely empty result for a valid window comes
    back as a normal HTTP 200 with items: [] instead, not this error.
    Detected by matching the message text since Ozon's error shape here is
    an opaque passthrough of an internal RPC error ({"code":3,"message":
    "...There is no data for the specified period"}), not a structured
    field this app can otherwise rely on."""
    message = str(exc).lower()
    return "no data for the specified period" in message or "getpremiumanalyticsperiod" in message


def _fetch_with_period_shrink(
    client: OzonSellerClient,
    *,
    date_from: date,
    date_to: date,
    skus: list[str],
    limit_by_sku: int,
    page_size: int,
    min_period_days: int,
    batch_num: int,
    total_batches: int,
) -> tuple[dict, date, date, list[int]]:
    """Ozon's real allowed date range for this endpoint isn't documented
    anywhere this app can reach (docs.ozon.ru/dev.ozon.ru/seller-edu.ozon.ru
    are all network-blocked from this environment, and no third-party
    source states an exact day count either — only that Premium/Premium
    Plus reportedly unlock "a longer period", with no number given) and
    plausibly depends on the account's own plan/tier anyway. Rather than
    hardcode a guessed limit, this discovers the largest ACCEPTED window
    empirically per run: keeps date_to fixed (closest to "now", where the
    data actually is) and halves the span, moving date_from forward, until
    Ozon accepts the request or the span reaches min_period_days — at which
    point the error is surfaced as-is rather than shrinking indefinitely.

    Returns (data, actual_date_from, actual_date_to, rejected_spans) —
    rejected_spans lists the span lengths (in days) Ozon turned down before
    the one that worked (or before giving up), for diagnostics."""
    current_from = date_from
    rejected_spans: list[int] = []
    while True:
        span_days = (date_to - current_from).days + 1
        # See this module's other TEMPORARY diagnostic logging for why this
        # is WARNING, not INFO — this deployment doesn't configure a log
        # level, so INFO would silently never appear in `docker compose logs`.
        logger.warning(
            "Позиции в поиске: батч %d/%d, sku=%s, date_from=%s, date_to=%s (%d дн.), limit_by_sku=%d, page_size=%d",
            batch_num, total_batches, skus, current_from.isoformat(), date_to.isoformat(), span_days,
            limit_by_sku, page_size,
        )
        try:
            data = client.get_product_query_details(
                date_from=f"{current_from.isoformat()}T00:00:00Z",
                date_to=f"{date_to.isoformat()}T23:59:59Z",
                skus=skus,
                limit_by_sku=limit_by_sku,
                page_size=page_size,
            )
            return data, current_from, date_to, rejected_spans
        except OzonAPIError as exc:
            if not _is_period_rejected_error(exc) or span_days <= min_period_days:
                raise
            rejected_spans.append(span_days)
            new_span = max(min_period_days, span_days // 2)
            current_from = date_to - timedelta(days=new_span - 1)


def _retry_empty_batch_per_sku(
    client: OzonSellerClient,
    *,
    data: dict,
    sku_batch: list[str],
    date_from_str: str,
    date_to_str: str,
    limit_by_sku: int,
    page_size: int,
    batch_num: int,
    errors: list[str],
) -> dict:
    """CONFIRMED LIVE: a group request for several SKUs can come back with
    items: [] even though ONE of those exact SKUs, requested ALONE with the
    identical date range, has real rows (confirmed independently via a
    manual XLSX export from Ozon's own cabinet). This module's own docstring
    already flagged batching >1 SKU per request as an unconfirmed
    assumption (only ever field-tested with 1 SKU) — this is now direct
    evidence it doesn't reliably work. Rather than silently accept an empty
    group response as "no data" and lose real rows, retry each SKU in the
    batch individually (same dates/limits) and merge whatever comes back.
    A no-op if the group response already had rows, or the batch was only
    ever 1 SKU (nothing left to split)."""
    if (data.get("items") or []) or len(sku_batch) <= 1:
        return data

    logger.warning(
        "Позиции в поиске: батч %d (%d SKU) вернул 0 items одним запросом — "
        "SEARCH_QUERY_STATS_SKU_BATCH_SIZE>1 не подтверждён Ozon как надёжный "
        "(см. докстринг модуля, п. 3b); повторяю каждый SKU по отдельности, "
        "чтобы не потерять реальные данные.",
        batch_num, len(sku_batch),
    )
    merged_items: list[dict] = []
    for sku in sku_batch:
        try:
            single = client.get_product_query_details(
                date_from=date_from_str,
                date_to=date_to_str,
                skus=[sku],
                limit_by_sku=limit_by_sku,
                page_size=page_size,
            )
        except OzonRateLimited as exc:
            # OzonSellerClient._post() already retries a 429 internally with
            # exponential backoff (currently 4 waits: 1, 2, 4, 8s) before
            # ever raising this — reaching here means Ozon kept rate-limiting
            # through all of that, confirmed live during a burst of many
            # single-SKU fallback requests in a row. Worth its own message:
            # this SKU is uncollected for RATE-LIMITING, not because it
            # genuinely has no data — a plain re-run later should recover it.
            errors.append(
                f"Батч {batch_num}, SKU {sku}: Ozon продолжает отвечать 429 Too Many Requests даже после "
                f"повторов с задержкой — этот SKU не собран в этом запуске из-за ограничения скорости "
                f"Ozon (не из-за отсутствия данных). Повторите синхронизацию позже."
            )
            continue
        except OzonAPIError as exc:
            errors.append(f"Батч {batch_num}, SKU {sku} (повтор по одному после пустого группового ответа): {exc}")
            continue
        single_items = single.get("items") or []
        logger.warning(
            "Позиции в поиске: батч %d, SKU %s по отдельности -> %d items",
            batch_num, sku, len(single_items),
        )
        merged_items.extend(single_items)

    merged = dict(data)
    merged["items"] = merged_items
    return merged


def _is_positive_sku(value: str | None) -> bool:
    """Mirrors Ozon's own validation for this endpoint ("Skus[N]: value
    must be greater than 0") — a real Ozon SKU is always a positive
    integer; 0 is Ozon's own sentinel for "no SKU assigned yet"."""
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _apply_row(stat: SearchQueryStatistic, item: dict, *, product: Product | None) -> None:
    stat.product_id = product.id if product else None
    stat.offer_id = product.offer_id if product else stat.offer_id
    stat.people_searched = item.get("unique_search_users")
    stat.people_saw = item.get("unique_view_users")
    stat.position_ozon = _to_decimal(item.get("position"))
    stat.conv_search_to_card_pct_ozon = _to_decimal(item.get("view_conversion"))
    stat.conv_search_to_order_pct_ozon = None
    stat.ordered_units_by_query = item.get("order_count")
    stat.ordered_sum_by_query_rub = _to_decimal(item.get("gmv"))
    stat.query_index = item.get("query_index")
    stat.raw_payload = None  # set by caller once json-serialized


def sync_search_query_details(
    db: Session,
    *,
    store_id: str,
    client: OzonSellerClient,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SyncOutcome:
    """Runs one full sync for one store: every known product SKU -> batches
    of <=SEARCH_QUERY_STATS_SKU_BATCH_SIZE -> sequential
    request/parse/save per batch, committing after each batch so a later
    batch's failure doesn't lose earlier ones. The caller owns SyncRun
    bookkeeping and the session's overall lifecycle."""
    settings = get_settings()
    outcome = SyncOutcome()

    today = datetime.now(timezone.utc).date()
    # Never default to "today" — Ozon's analytics aggregation lags behind it
    # (see this module's docstring, point 1b) and rejects a period ending
    # today with "There is no data for the specified period".
    resolved_date_to = date_to or (today - timedelta(days=settings.SEARCH_QUERY_STATS_DATA_LAG_DAYS))
    resolved_date_from = date_from or (
        resolved_date_to - timedelta(days=settings.SEARCH_QUERY_STATS_DEFAULT_LOOKBACK_DAYS - 1)
    )
    # Confirmed live: a bare "YYYY-MM-DD" is rejected with "invalid
    # google.protobuf.Timestamp value" — a full ISO timestamp is required.
    date_from_str = f"{resolved_date_from.isoformat()}T00:00:00Z"
    date_to_str = f"{resolved_date_to.isoformat()}T23:59:59Z"

    products = db.query(Product).filter(Product.store_id == store_id, Product.is_archived.is_(False)).all()
    if not products:
        outcome.errors.append("Нет товаров для запроса статистики поисковых запросов — сначала синхронизируйте товары.")
        return outcome

    # Defensive, not redundant: Ozon's own product-info API can hand back
    # sku=0 as a "no SKU assigned yet" sentinel (see the fix in
    # app.api.routes.sync's product sync for the confirmed root cause), and
    # a store synced before that fix can still have such a row sitting in
    # Product. Ozon's own validation for this endpoint rejects the entire
    # batch containing one ("Skus[N]: value must be greater than 0"), so a
    # single bad row would otherwise silently fail every other product in
    # its batch too. Before giving up on them, retry via offer_id — same
    # fallback as the product catalog sync (see _resolve_zero_sku_products).
    valid_products = [p for p in products if _is_positive_sku(p.ozon_sku)]
    invalid_products = [p for p in products if not _is_positive_sku(p.ozon_sku)]
    if invalid_products:
        resolved, invalid_products = _resolve_zero_sku_products(db, store_id, client, invalid_products)
        if resolved:
            db.flush()  # persist corrected ozon_sku so it's usable below and in the wider catalog
            valid_products.extend(resolved)
    if invalid_products:
        # Name each one explicitly (sku/name/offer_id/internal id) rather
        # than just a count — a bare count gave no way to tell which
        # product to check on Ozon's side, or whether re-syncing the
        # catalog had actually fixed it. Capped at 10 to keep the message
        # readable if a store somehow has many.
        named = "; ".join(
            f"«{p.name}» (SKU={p.ozon_sku!r}, offer_id={p.offer_id!r}, product_id={p.id})"
            for p in invalid_products[:10]
        )
        more = f" и ещё {len(invalid_products) - 10}" if len(invalid_products) > 10 else ""
        outcome.errors.append(
            f"Пропущено {len(invalid_products)} товар(ов) с некорректным SKU (0/пусто): {named}{more} — "
            f"это значение приходит от самого Ozon (sku=0 значит «SKU ещё не назначен», обычно для товара вне "
            f"активной схемы FBO/FBS); проверьте товар в кабинете Ozon и повторите синхронизацию каталога "
            f"(POST /sync/ozon-products) после того, как Ozon назначит ему реальный SKU."
        )

    product_by_sku = {p.ozon_sku: p for p in valid_products}
    skus = list(product_by_sku.keys())
    if not skus:
        outcome.errors.append("Нет товаров с корректным SKU для запроса статистики поисковых запросов.")
        return outcome

    sku_batches = _batched(skus, settings.SEARCH_QUERY_STATS_SKU_BATCH_SIZE)
    total_batches = len(sku_batches)

    # Resolve the actually-usable date window using the first batch, BEFORE
    # existing_keys is built (it's keyed by period_start/period_end) and
    # before any row is saved — Ozon can reject the originally-resolved
    # window outright (see _fetch_with_period_shrink), not just return it
    # empty, so the real period may only be known after this call.
    first_data = None
    try:
        first_data, resolved_date_from, resolved_date_to, rejected_spans = _fetch_with_period_shrink(
            client,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            skus=sku_batches[0],
            limit_by_sku=settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU,
            page_size=settings.SEARCH_QUERY_STATS_PAGE_SIZE,
            min_period_days=settings.SEARCH_QUERY_STATS_MIN_PERIOD_DAYS,
            batch_num=1,
            total_batches=total_batches,
        )
        if rejected_spans:
            outcome.errors.append(
                f"Ozon отклонил запрошенный период как слишком длинный/старый для этой статистики "
                f"(пробовали {', '.join(f'{s} дн.' for s in rejected_spans)} — ошибка «нет данных за указанный "
                f"период», это ограничение периода/тарифа Ozon, а не отсутствие данных). Период автоматически "
                f"сокращён до {resolved_date_from.isoformat()}..{resolved_date_to.isoformat()} "
                f"({(resolved_date_to - resolved_date_from).days + 1} дн.) — именно за него и сохранена статистика. "
                f"Для более глубокой истории запрашивайте её отдельными более короткими периодами."
            )
    except OzonAPIError as exc:
        if _is_period_rejected_error(exc):
            outcome.errors.append(
                f"Ozon отклоняет любой период для этой статистики вплоть до минимального "
                f"{settings.SEARCH_QUERY_STATS_MIN_PERIOD_DAYS} дн. с ошибкой «нет данных за указанный период» — "
                f"это ограничение периода/тарифа Ozon (Premium/Premium Plus) для "
                f"/v1/analytics/product-queries/details, а не отсутствие данных. Доступны, по всей видимости, "
                f"только более свежие и/или совсем короткие периоды — уточните точный лимит вашего тарифа в "
                f"поддержке Ozon, либо запросите статистику вручную за пробный период: "
                f"POST /sync/ozon-search-query-statistics?date_from=ГГГГ-ММ-ДД&date_to=ГГГГ-ММ-ДД."
            )
        else:
            outcome.errors.append(f"Батч 1 ({len(sku_batches[0])} SKU): {exc}")

    date_from_str = f"{resolved_date_from.isoformat()}T00:00:00Z"
    date_to_str = f"{resolved_date_to.isoformat()}T23:59:59Z"

    existing_keys = {
        (s.ozon_sku, s.query_text)
        for s in db.query(SearchQueryStatistic.ozon_sku, SearchQueryStatistic.query_text)
        .filter(
            SearchQueryStatistic.store_id == store_id,
            SearchQueryStatistic.period_start == resolved_date_from,
            SearchQueryStatistic.period_end == resolved_date_to,
        )
        .all()
    }

    def _process_batch(batch_num: int, sku_batch: list[str], data: dict) -> None:
        # TEMPORARY diagnostic logging (see task: "получено 0, создано 0,
        # обновлено 0" on every batch despite HTTP 200) — logs Ozon's raw
        # response so a live run can show whether Ozon is genuinely
        # returning an empty items array, or a differently-shaped body this
        # parsing code doesn't recognize. Remove once the root cause here is
        # confirmed and fixed.
        items = data.get("items") or []
        if not items:
            # Full raw body — safe to log in full here (no per-row PII/
            # financial data to worry about when there are no rows).
            logger.warning(
                "Позиции в поиске: батч %d — Ozon вернул 0 items. Полный ответ: %s",
                batch_num, json.dumps(data, ensure_ascii=False),
            )
        else:
            logger.warning(
                "Позиции в поиске: батч %d — получено %d items (total=%s, page_count=%s). Первая строка: %s",
                batch_num, len(items), data.get("total"), data.get("page_count"),
                json.dumps(items[0], ensure_ascii=False),
            )
        # Diagnostic, not a guess at a fix: the SKU batch size is sized to
        # avoid ever needing pagination (see this module's docstring) — if
        # Ozon still reports more pages than fit in one response, surface it
        # instead of silently dropping rows past page 1.
        page_count = data.get("page_count")
        if isinstance(page_count, int) and page_count > 1:
            outcome.errors.append(
                f"Батч {batch_num} ({len(sku_batch)} SKU): ответ содержит {page_count} страниц, "
                f"а пагинация не реализована — часть строк могла быть пропущена. "
                f"Уменьшите SEARCH_QUERY_STATS_SKU_BATCH_SIZE."
            )

        for item in items:
            outcome.fetched += 1
            sku = str(item.get("sku", ""))
            query_text = item.get("query")
            if not sku or not query_text:
                outcome.errors.append(f"Батч {batch_num}: строка без sku/query — пропущена ({item!r})")
                continue

            currency = item.get("currency")
            if currency and currency != "RUB":
                outcome.errors.append(f"SKU {sku}, запрос «{query_text}»: неожиданная валюта {currency} (ожидался RUB)")

            product = product_by_sku.get(sku)
            key = (sku, query_text)
            raw_payload = json.dumps(item, ensure_ascii=False)

            if key in existing_keys:
                existing = (
                    db.query(SearchQueryStatistic)
                    .filter(
                        SearchQueryStatistic.store_id == store_id,
                        SearchQueryStatistic.ozon_sku == sku,
                        SearchQueryStatistic.query_text == query_text,
                        SearchQueryStatistic.period_start == resolved_date_from,
                        SearchQueryStatistic.period_end == resolved_date_to,
                    )
                    .first()
                )
                if existing is None:
                    # existing_keys said this row exists, but autoflush is
                    # off (see db/session.py) — a row only db.add()-ed
                    # earlier in this same loop isn't visible to a plain
                    # SELECT without an intervening flush. Flush and retry
                    # once rather than crashing the whole batch on a None.
                    db.flush()
                    existing = (
                        db.query(SearchQueryStatistic)
                        .filter(
                            SearchQueryStatistic.store_id == store_id,
                            SearchQueryStatistic.ozon_sku == sku,
                            SearchQueryStatistic.query_text == query_text,
                            SearchQueryStatistic.period_start == resolved_date_from,
                            SearchQueryStatistic.period_end == resolved_date_to,
                        )
                        .first()
                    )
                if existing is None:
                    outcome.errors.append(
                        f"SKU {sku}, запрос «{query_text}»: не удалось найти существующую строку для обновления — пропущено"
                    )
                    continue
                _apply_row(existing, item, product=product)
                existing.raw_payload = raw_payload
                outcome.updated += 1
            else:
                stat = SearchQueryStatistic(
                    store_id=store_id,
                    ozon_sku=sku,
                    query_text=query_text,
                    period_start=resolved_date_from,
                    period_end=resolved_date_to,
                    source=_SOURCE,
                )
                _apply_row(stat, item, product=product)
                stat.raw_payload = raw_payload
                db.add(stat)
                existing_keys.add(key)
                outcome.created += 1

        db.commit()

    if first_data is not None:
        first_data = _retry_empty_batch_per_sku(
            client, data=first_data, sku_batch=sku_batches[0],
            date_from_str=date_from_str, date_to_str=date_to_str,
            limit_by_sku=settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU,
            page_size=settings.SEARCH_QUERY_STATS_PAGE_SIZE,
            batch_num=1, errors=outcome.errors,
        )
        _process_batch(1, sku_batches[0], first_data)

    for batch_num, sku_batch in enumerate(sku_batches[1:], start=2):
        logger.warning(
            "Позиции в поиске: батч %d/%d, sku=%s, date_from=%s, date_to=%s, limit_by_sku=%d, page_size=%d",
            batch_num, total_batches, sku_batch, date_from_str, date_to_str,
            settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU, settings.SEARCH_QUERY_STATS_PAGE_SIZE,
        )
        try:
            data = client.get_product_query_details(
                date_from=date_from_str,
                date_to=date_to_str,
                skus=sku_batch,
                limit_by_sku=settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU,
                page_size=settings.SEARCH_QUERY_STATS_PAGE_SIZE,
            )
        except OzonAPIError as exc:
            outcome.errors.append(f"Батч {batch_num} ({len(sku_batch)} SKU): {exc}")
            continue
        data = _retry_empty_batch_per_sku(
            client, data=data, sku_batch=sku_batch,
            date_from_str=date_from_str, date_to_str=date_to_str,
            limit_by_sku=settings.SEARCH_QUERY_STATS_LIMIT_BY_SKU,
            page_size=settings.SEARCH_QUERY_STATS_PAGE_SIZE,
            batch_num=batch_num, errors=outcome.errors,
        )
        _process_batch(batch_num, sku_batch, data)

    if outcome.fetched == 0 and not outcome.errors:
        # Every batch returned HTTP 200 with an empty items array — not an
        # error Ozon reports explicitly, so nothing above would otherwise
        # surface it. The period-rejection case (Ozon refusing the window
        # outright) is handled separately above; reaching here means Ozon
        # accepted the window but had nothing to return for it. Most likely
        # causes, in order of likelihood: (1) Ozon genuinely has no
        # search-query data for these SKUs in [date_from, date_to] — the
        # store has too little search traffic in this window; (2) the
        # account's Ozon plan doesn't unlock this data for the requested
        # period. See this run's own log output (batch-level raw response is
        # logged above) to tell these apart.
        outcome.errors.append(
            f"Ozon вернул 0 строк за весь период {resolved_date_from.isoformat()}..{resolved_date_to.isoformat()} "
            f"по всем {len(skus)} SKU, хотя ответ был успешным (HTTP 200) — это не ошибка запроса. Возможные причины: "
            f"(1) у этих товаров действительно нет данных по поисковым запросам за этот период в кабинете Ozon; "
            f"(2) тариф магазина не открывает эти данные за такой период. Проверьте вручную отчёт "
            f"«Аналитика → Товары в поиске → Запросы моего товара» в кабинете Ozon за этот же период — если там "
            f"данные есть, а тут нет, откройте подробный лог этого запуска (сырой ответ Ozon логируется на уровне "
            f"WARNING)."
        )

    return outcome
