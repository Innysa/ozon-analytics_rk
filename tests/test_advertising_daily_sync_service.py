"""Tests for the orchestration service that drives Ozon Performance API's
async statistics-report flow across batches of campaigns (see
app.services.advertising_daily_sync_service): batching by
ADVERTISING_STATS_BATCH_SIZE, sequential per-batch processing, "report busy"
retry, and one batch's failure not aborting the others. Uses a fake,
duck-typed OzonPerformanceClient — the client's own HTTP behavior is already
covered by test_ozon_performance_statistics_client.py, and the CSV/ZIP
parsing by test_advertising_daily_statistic_parser.py."""
import io
import zipfile
from datetime import date

from app.services.advertising_daily_sync_service import sync_advertising_daily_statistics
from app.services.ozon_performance.exceptions import OzonPerformanceAPIError, OzonPerformanceReportBusy
from app.services.ozon_performance.schemas import OzonStatisticsReportStatus

_HEADER = (
    "SKU;Название товара;День;Цена товара;Тип страницы;Условие показа;Показы;Клики;"
    "CTR (%);В корзину;Средняя ставка (руб.);Расход, ₽ с НДС;Продано товаров;"
    "Продажи в продвижении, ₽;ДРР в продвижении, %;ДРР (общий), %"
)


def _zip_for_ids(ids: list[str], *, day: str = "01.09.2026") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for oid in ids:
            row = f"sku{oid};Товар {oid};{day};100;PDP;search;10;1;1,0;0;1,00;1,00;0;0;0;0"
            csv_text = "\n".join(["Статистика; период", _HEADER, row])
            zf.writestr(f"{oid}_{day}-{day}.csv", csv_text.encode("utf-8-sig"))
    return buf.getvalue()


class FakeOzonPerformanceClient:
    def __init__(self, zips_by_uuid: dict[str, bytes], *, busy_first_n: int = 0):
        self.create_calls: list[tuple[str, ...]] = []
        self._zips_by_uuid = zips_by_uuid
        self._busy_remaining = busy_first_n

    def create_statistics_report(self, campaign_ids, date_from, date_to):
        self.create_calls.append(tuple(campaign_ids))
        if self._busy_remaining > 0:
            self._busy_remaining -= 1
            raise OzonPerformanceReportBusy("Превышен лимит активных запросов (максимум 1)")
        return "uuid:" + ",".join(campaign_ids)

    def get_statistics_report_status(self, uuid: str) -> OzonStatisticsReportStatus:
        return OzonStatisticsReportStatus(state="OK", link=uuid)

    def download_statistics_report(self, link: str) -> bytes:
        return self._zips_by_uuid[link]


def _make_running_campaign(db_session, store_id: str, ozon_campaign_id: str):
    from app.models.advertising_campaign import AdvertisingCampaign

    campaign = AdvertisingCampaign(
        store_id=store_id, ozon_campaign_id=ozon_campaign_id, name=f"Кампания {ozon_campaign_id}",
        state="CAMPAIGN_STATE_RUNNING",
    )
    db_session.add(campaign)
    db_session.flush()
    return campaign


def test_no_active_campaigns_reports_a_clear_error(db_session, two_stores_with_users):
    d = two_stores_with_users
    client = FakeOzonPerformanceClient({})

    outcome = sync_advertising_daily_statistics(db_session, store_id=d["store_a"].id, client=client)

    assert outcome.created == 0
    assert len(outcome.errors) == 1
    assert "активных кампаний" in outcome.errors[0]


def test_paused_campaign_is_not_synced(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    from app.models.advertising_campaign import AdvertisingCampaign
    paused = AdvertisingCampaign(
        store_id=d["store_a"].id, ozon_campaign_id="222", name="Пауза", state="CAMPAIGN_STATE_STOPPED",
    )
    db_session.add(paused)
    db_session.flush()

    client = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"])})
    sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert client.create_calls == [("111",)]


def test_single_batch_creates_rows(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    client = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"])})

    outcome = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert outcome.created == 1
    assert outcome.updated == 0
    assert outcome.errors == []
    assert client.create_calls == [("111",)]

    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    row = db_session.query(AdvertisingDailyStatistic).filter(
        AdvertisingDailyStatistic.store_id == d["store_a"].id
    ).one()
    assert row.ozon_sku == "sku111"
    assert row.impressions == 10
    assert float(row.spend_rub) == 1.0
    assert row.campaign_id is not None  # matched back to the AdvertisingCampaign row


def test_orders_revenue_and_drr_are_parsed_from_the_real_confirmed_columns(db_session, two_stores_with_users):
    """Regression test for the real bug: an earlier version of the column map
    used "Заказы"/"Выручка, ₽" (guessed from the unrelated CSV-upload export)
    and every row parsed with orders=None/revenue_rub=None even though
    spend/impressions/clicks worked fine — confirmed on a real account via
    raw_payload. The real labels are "Продано товаров"/"Продажи в
    продвижении, ₽"/"ДРР в продвижении, %"/"ДРР (общий), %"."""
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    row = "sku111;Товар 111;01.09.2026;100;PDP;search;10;1;1,0;0;1,00;500,00;1;2750,00;4,1;5,0"
    csv_text = "\n".join(["Статистика; период", _HEADER, row])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("111_01.09.2026-01.09.2026.csv", csv_text.encode("utf-8-sig"))
    client = FakeOzonPerformanceClient({"uuid:111": buf.getvalue()})

    sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    row_out = db_session.query(AdvertisingDailyStatistic).filter(
        AdvertisingDailyStatistic.store_id == d["store_a"].id
    ).one()
    assert row_out.orders == 1
    assert float(row_out.revenue_rub) == 2750.00
    assert float(row_out.drr_promo_pct_ozon) == 4.1
    assert float(row_out.drr_total_pct_ozon) == 5.0


def test_warns_when_report_has_fewer_days_than_requested(db_session, two_stores_with_users):
    """If Ozon's CSV comes back with fewer distinct days than the requested
    range, that must show up as a warning in outcome.errors (and therefore in
    the SyncRun's error_message / the frontend's notice) rather than being
    silently accepted — this is exactly the symptom a real account hit."""
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    # Only one day's row in the CSV, but a 7-day range is requested below.
    client = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"], day="07.09.2026")})

    outcome = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 7),
    )

    assert outcome.created == 1
    assert any("в отчёте 1 дн. из 7 запрошенных" in e for e in outcome.errors)


def test_reupload_of_same_day_updates_instead_of_duplicating(db_session, two_stores_with_users):
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    client = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"])})
    sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    client2 = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"])})
    outcome2 = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client2, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert outcome2.created == 0
    assert outcome2.updated == 1

    from app.models.advertising_daily_statistic import AdvertisingDailyStatistic
    count = db_session.query(AdvertisingDailyStatistic).filter(
        AdvertisingDailyStatistic.store_id == d["store_a"].id
    ).count()
    assert count == 1


def test_more_than_batch_size_campaigns_are_split_into_sequential_batches(db_session, two_stores_with_users):
    """Ozon's own hard limit is 10 campaign ids per statistics-report
    request — 15 active campaigns must become 2 requests (10 + 5), never 1."""
    d = two_stores_with_users
    ozon_ids = [str(100 + i) for i in range(15)]
    for oid in ozon_ids:
        _make_running_campaign(db_session, d["store_a"].id, oid)
    db_session.flush()

    batch1_ids, batch2_ids = ozon_ids[:10], ozon_ids[10:]
    uuid1, uuid2 = "uuid:" + ",".join(batch1_ids), "uuid:" + ",".join(batch2_ids)
    client = FakeOzonPerformanceClient({uuid1: _zip_for_ids(batch1_ids), uuid2: _zip_for_ids(batch2_ids)})

    outcome = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert len(client.create_calls) == 2
    assert len(client.create_calls[0]) == 10
    assert len(client.create_calls[1]) == 5
    assert outcome.created == 15


def test_report_busy_error_is_retried_until_success(monkeypatch, db_session, two_stores_with_users):
    """Real error text confirmed against a real account: only 1 report may be
    in flight per Performance API account — a "busy" response must be
    retried, not treated as a fatal error for the whole sync."""
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    import app.services.advertising_daily_sync_service as sync_service
    monkeypatch.setattr(sync_service.time, "sleep", lambda *_: None)

    client = FakeOzonPerformanceClient({"uuid:111": _zip_for_ids(["111"])}, busy_first_n=2)

    outcome = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert outcome.created == 1
    assert len(client.create_calls) == 3  # 2 busy attempts + 1 success


def test_one_batch_failing_does_not_abort_the_others(db_session, two_stores_with_users):
    d = two_stores_with_users
    ozon_ids = [str(100 + i) for i in range(15)]
    for oid in ozon_ids:
        _make_running_campaign(db_session, d["store_a"].id, oid)
    db_session.flush()

    batch1_ids, batch2_ids = ozon_ids[:10], ozon_ids[10:]

    class FirstBatchFailsClient(FakeOzonPerformanceClient):
        def create_statistics_report(self, campaign_ids, date_from, date_to):
            if list(campaign_ids) == batch1_ids:
                raise OzonPerformanceAPIError("Ozon вернул ошибку сервера 500")
            return super().create_statistics_report(campaign_ids, date_from, date_to)

    uuid2 = "uuid:" + ",".join(batch2_ids)
    client = FirstBatchFailsClient({uuid2: _zip_for_ids(batch2_ids)})

    outcome = sync_advertising_daily_statistics(
        db_session, store_id=d["store_a"].id, client=client,
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 1),
    )

    assert outcome.created == 5  # only the second batch's 5 campaigns
    assert len(outcome.errors) == 1
    assert "Ozon вернул ошибку" in outcome.errors[0]


def test_default_date_range_is_last_n_days_when_not_specified(db_session, two_stores_with_users):
    """No date_from/date_to passed -> the service must fall back to
    ADVERTISING_STATS_DEFAULT_LOOKBACK_DAYS, not fail or fetch everything."""
    d = two_stores_with_users
    _make_running_campaign(db_session, d["store_a"].id, "111")
    db_session.flush()

    captured = {}

    class CapturingClient(FakeOzonPerformanceClient):
        def create_statistics_report(self, campaign_ids, date_from, date_to):
            captured["date_from"] = date_from
            captured["date_to"] = date_to
            return super().create_statistics_report(campaign_ids, date_from, date_to)

    client = CapturingClient({"uuid:111": _zip_for_ids(["111"])})
    sync_advertising_daily_statistics(db_session, store_id=d["store_a"].id, client=client)

    assert "date_from" in captured and "date_to" in captured
    # ISO (YYYY-MM-DD) — confirmed live: Ozon rejects DD.MM.YYYY here with
    # {"error": "bad request parameter: dateFrom"} even though that's the
    # date format used inside the downloaded report's own filename.
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", captured["date_from"])
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", captured["date_to"])
