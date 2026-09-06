"""Orchestrates the automatic daily advertising-statistics sync from Ozon
Performance API's asynchronous statistics-report flow. This is the module
app.services.ozon_performance.client's own docstring points to for the full
write-up of every field-tested constraint of that flow (confirmed against a
real account before this was built):

  1. POST /api/client/statistics accepts at most 10 campaign ids per request
     (Ozon's own hard limit — real error text observed for over-limit
     requests: "Превышен лимит по количеству кампаний"). Campaigns are
     therefore processed in batches of ADVERTISING_STATS_BATCH_SIZE (10).

  2. Only 1 report may be in flight per Performance API account at a time.
     Requesting a 2nd concurrently fails with a confirmed real error text —
     "Превышен лимит активных запросов (максимум 1)" — mapped by
     OzonPerformanceClient to OzonPerformanceReportBusy. Because of this,
     batches are processed strictly SEQUENTIALLY: create report for batch N,
     poll until done, download + parse + save, commit, and only THEN create
     the report for batch N+1. Never in parallel, even across stores sharing
     nothing — this module still processes one store's batches serially
     because they share the same Performance API account.

  3. Report generation is async: state starts "IN_PROGRESS" and becomes
     "OK" (with a `link` field to download from) or "ERROR" (with an
     `error` field) — see OzonStatisticsReportStatus. Polled every
     ADVERTISING_STATS_POLL_INTERVAL_SECONDS up to a
     ADVERTISING_STATS_POLL_TIMEOUT_SECONDS ceiling per batch.

  4. The OAuth token expires after ~1800s; OzonPerformanceClient already
     refreshes it a bit early and on any 401, so a token expiring mid-poll
     is handled for free by the client itself, not specially here.

  5. The finished report is a ZIP of one CSV per requested campaign — see
     app.services.advertising_daily_statistic_parser for the exact layout,
     encoding handling, and "Всего" totals-row skip.

Default date range (when the caller doesn't pass one — e.g. the daily
scheduler): the last ADVERTISING_STATS_DEFAULT_LOOKBACK_DAYS days. Re-pulling
a short trailing window on every run (instead of only "yesterday") means a
single failed run doesn't create a permanent gap — the next run's window
still covers it. Rows are upserted (matched on store+campaign+sku+date), so
re-pulling the same days is safe and just refreshes numbers Ozon may still
be finalizing.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.advertising_campaign import AdvertisingCampaign
from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
from app.models.product import Product
from app.services.advertising_daily_statistic_parser import parse_statistics_report_zip
from app.services.ozon_performance.client import OzonPerformanceClient
from app.services.ozon_performance.exceptions import (
    OzonPerformanceAPIError,
    OzonPerformanceReportBusy,
    OzonPerformanceReportFailed,
    OzonPerformanceReportTimeout,
)

logger = logging.getLogger(__name__)


@dataclass
class SyncOutcome:
    fetched: int = 0
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _create_report_with_retry(
    client: OzonPerformanceClient,
    campaign_ids: list[str],
    date_from: str,
    date_to: str,
    *,
    max_attempts: int = 3,
    retry_wait_s: int = 30,
) -> str:
    """Ozon allows only 1 report in flight per account. If a previous run's
    report never finished polling (e.g. a crashed process, or two syncs
    overlapping), the create call fails as "busy" — wait and retry a few
    times rather than failing the whole batch on the first try."""
    last_exc: OzonPerformanceReportBusy | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.create_statistics_report(campaign_ids, date_from, date_to)
        except OzonPerformanceReportBusy as exc:
            last_exc = exc
            logger.warning("Реклама: отчёт занят (попытка %d/%d), жду %ds", attempt, max_attempts, retry_wait_s)
            if attempt < max_attempts:
                time.sleep(retry_wait_s)
    assert last_exc is not None
    raise last_exc


def _wait_for_report(client: OzonPerformanceClient, uuid: str, *, poll_interval: int, timeout: int) -> str:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get_statistics_report_status(uuid)
        state = (status.state or "").upper()
        if state == "OK" and status.link:
            return status.link
        if state == "ERROR":
            raise OzonPerformanceReportFailed(status.error or "Ozon сообщил об ошибке формирования отчёта")
        if time.monotonic() >= deadline:
            raise OzonPerformanceReportTimeout(f"Отчёт {uuid} не был готов за {timeout} секунд")
        time.sleep(poll_interval)


def _apply_row(stat: AdvertisingDailyStatistic, row: dict, *, product: Product, campaign: AdvertisingCampaign | None) -> None:
    stat.product_id = product.id
    stat.campaign_id = campaign.id if campaign else None
    stat.product_name = row.get("product_name")
    stat.product_price_rub = row.get("product_price_rub")
    stat.page_type = row.get("page_type")
    stat.impression_condition = row.get("impression_condition")
    stat.impressions = row.get("impressions")
    stat.clicks = row.get("clicks")
    stat.ctr_pct_ozon = row.get("ctr_pct_ozon")
    stat.cart_additions = row.get("cart_additions")
    stat.avg_bid_rub_ozon = row.get("avg_bid_rub_ozon")
    stat.spend_rub = row.get("spend_rub")
    stat.orders = row.get("orders")
    stat.revenue_rub = row.get("revenue_rub")
    stat.orders_model = row.get("orders_model")
    stat.revenue_model_rub = row.get("revenue_model_rub")
    stat.raw_payload = row.get("raw_payload")


def sync_advertising_daily_statistics(
    db: Session,
    *,
    store_id: str,
    client: OzonPerformanceClient,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SyncOutcome:
    """Runs one full sync for one store: active campaigns -> batches of
    <=ADVERTISING_STATS_BATCH_SIZE -> sequential request/poll/download/parse/
    save per batch, committing after each batch so a later batch's failure
    doesn't lose earlier ones. The caller owns SyncRun bookkeeping and the
    session's overall lifecycle (this function does call db.commit() per
    batch, since batches must be durable before the next one starts)."""
    settings = get_settings()
    outcome = SyncOutcome()

    today = datetime.now(timezone.utc).date()
    resolved_date_to = date_to or today
    resolved_date_from = date_from or (resolved_date_to - timedelta(days=settings.ADVERTISING_STATS_DEFAULT_LOOKBACK_DAYS - 1))
    date_from_str = resolved_date_from.strftime("%d.%m.%Y")
    date_to_str = resolved_date_to.strftime("%d.%m.%Y")

    campaigns = (
        db.query(AdvertisingCampaign)
        .filter(AdvertisingCampaign.store_id == store_id, AdvertisingCampaign.state == "CAMPAIGN_STATE_RUNNING")
        .all()
    )
    if not campaigns:
        outcome.errors.append(
            "Нет активных кампаний (CAMPAIGN_STATE_RUNNING) — сначала синхронизируйте список кампаний."
        )
        return outcome

    campaign_by_ozon_id = {c.ozon_campaign_id: c for c in campaigns}
    product_cache: dict[str, Product] = {
        p.ozon_sku: p for p in db.query(Product).filter(Product.store_id == store_id).all()
    }
    existing_keys = {
        (s.ozon_campaign_id, s.ozon_sku, s.date)
        for s in db.query(
            AdvertisingDailyStatistic.ozon_campaign_id,
            AdvertisingDailyStatistic.ozon_sku,
            AdvertisingDailyStatistic.date,
        ).filter(AdvertisingDailyStatistic.store_id == store_id).all()
    }

    campaign_ids = list(campaign_by_ozon_id.keys())
    for batch_num, batch in enumerate(_batched(campaign_ids, settings.ADVERTISING_STATS_BATCH_SIZE), start=1):
        logger.info("Реклама: батч %d — запрашиваю отчёт для %d кампаний", batch_num, len(batch))
        try:
            uuid = _create_report_with_retry(client, batch, date_from_str, date_to_str)
            link = _wait_for_report(
                client,
                uuid,
                poll_interval=settings.ADVERTISING_STATS_POLL_INTERVAL_SECONDS,
                timeout=settings.ADVERTISING_STATS_POLL_TIMEOUT_SECONDS,
            )
            zip_bytes = client.download_statistics_report(link)
        except OzonPerformanceAPIError as exc:
            outcome.errors.append(f"Батч {batch_num} ({', '.join(batch)}): {exc}")
            continue

        parsed = parse_statistics_report_zip(zip_bytes)
        outcome.errors.extend(parsed.warnings)

        for row in parsed.rows:
            outcome.fetched += 1
            ozon_campaign_id = row["ozon_campaign_id"]
            sku = row["sku"]
            row_date = row["date"]
            key = (ozon_campaign_id, sku, row_date)

            campaign = campaign_by_ozon_id.get(ozon_campaign_id)
            product = product_cache.get(sku)
            if not product:
                product = Product(store_id=store_id, ozon_sku=sku, name=row.get("product_name") or f"Товар SKU {sku}")
                db.add(product)
                db.flush()
                product_cache[sku] = product

            if key in existing_keys:
                existing = (
                    db.query(AdvertisingDailyStatistic)
                    .filter(
                        AdvertisingDailyStatistic.store_id == store_id,
                        AdvertisingDailyStatistic.ozon_campaign_id == ozon_campaign_id,
                        AdvertisingDailyStatistic.ozon_sku == sku,
                        AdvertisingDailyStatistic.date == row_date,
                    )
                    .first()
                )
                _apply_row(existing, row, product=product, campaign=campaign)
                outcome.updated += 1
            else:
                stat = AdvertisingDailyStatistic(
                    store_id=store_id,
                    ozon_campaign_id=ozon_campaign_id,
                    ozon_sku=sku,
                    date=row_date,
                    source="ozon_performance_api",
                )
                _apply_row(stat, row, product=product, campaign=campaign)
                db.add(stat)
                existing_keys.add(key)
                outcome.created += 1

        db.commit()

    return outcome
