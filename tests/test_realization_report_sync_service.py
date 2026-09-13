"""Tests for app.services.realization_report_sync_service — syncing Ozon's
official monthly settlement report (POST /v2/finance/realization). No real
HTTP: a tiny duck-typed fake client stands in for OzonSellerClient, same
convention as every other sync service's tests in this project."""
from datetime import date

from app.models.realization_report_month import RealizationReportMonth
from app.services.ozon.exceptions import OzonAPIError, OzonFeatureUnavailable
from app.services.realization_report_sync_service import (
    _closed_months_before,
    sync_missing_realization_reports,
    sync_realization_report_month,
)


class _FakeClient:
    def __init__(self, *, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls: list[tuple[int, int]] = []

    def get_realization_report(self, *, year: int, month: int):
        self.calls.append((year, month))
        if self._raise_exc:
            raise self._raise_exc
        return self._response


def test_closed_months_before_never_includes_current_month():
    """CONFIRMED 2026-09-13: Ozon 404s the current, still-open month — this
    helper must never even try it."""
    months = _closed_months_before(today=date(2026, 9, 13), count=3)
    assert months == [(2026, 8), (2026, 7), (2026, 6)]


def test_closed_months_before_wraps_across_year_boundary():
    months = _closed_months_before(today=date(2026, 2, 1), count=3)
    assert months == [(2026, 1), (2025, 12), (2025, 11)]


def test_sync_realization_report_month_creates_new_row(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(response={"result": {"rows": [{"offer_id": "x"}]}})

    outcome = sync_realization_report_month(db_session, store_id=store_id, client=client, year=2026, month=8)

    assert outcome.fetched is True
    assert outcome.created is True
    assert outcome.not_yet_available is False
    assert outcome.error is None
    row = db_session.query(RealizationReportMonth).filter(
        RealizationReportMonth.store_id == store_id, RealizationReportMonth.year == 2026, RealizationReportMonth.month == 8,
    ).one()
    assert "offer_id" in row.raw_payload


def test_sync_realization_report_month_updates_existing_row(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(RealizationReportMonth(store_id=store_id, year=2026, month=8, raw_payload="old", source="ozon_seller_api"))
    db_session.commit()

    client = _FakeClient(response={"result": {"rows": [{"offer_id": "new"}]}})
    outcome = sync_realization_report_month(db_session, store_id=store_id, client=client, year=2026, month=8)

    assert outcome.fetched is True
    assert outcome.created is False
    rows = db_session.query(RealizationReportMonth).filter(
        RealizationReportMonth.store_id == store_id, RealizationReportMonth.year == 2026, RealizationReportMonth.month == 8,
    ).all()
    assert len(rows) == 1  # upserted, not duplicated
    assert "new" in rows[0].raw_payload


def test_sync_realization_report_month_treats_404_as_not_yet_available(db_session, two_stores_with_users):
    """CONFIRMED 2026-09-13: Ozon answers "Report was not found" (404,
    raised as OzonFeatureUnavailable by OzonSellerClient._post()) for the
    current, still-open month — this is an expected, non-error outcome,
    not something to alert on."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(raise_exc=OzonFeatureUnavailable("Ozon вернул 404 ... Report was not found"))

    outcome = sync_realization_report_month(db_session, store_id=store_id, client=client, year=2026, month=9)

    assert outcome.fetched is False
    assert outcome.not_yet_available is True
    assert outcome.error is not None
    assert db_session.query(RealizationReportMonth).count() == 0


def test_sync_realization_report_month_surfaces_real_errors(db_session, two_stores_with_users):
    d = two_stores_with_users
    store_id = d["store_a"].id
    client = _FakeClient(raise_exc=OzonAPIError("Ozon вернул ошибку сервера 500"))

    outcome = sync_realization_report_month(db_session, store_id=store_id, client=client, year=2026, month=8)

    assert outcome.fetched is False
    assert outcome.not_yet_available is False
    assert "500" in outcome.error


def test_sync_missing_realization_reports_skips_already_archived_months(db_session, two_stores_with_users):
    """Idempotent: a month already archived must not be re-fetched from
    Ozon on every daily scheduler tick or manual click."""
    d = two_stores_with_users
    store_id = d["store_a"].id
    db_session.add(RealizationReportMonth(store_id=store_id, year=2026, month=8, raw_payload="{}", source="ozon_seller_api"))
    db_session.commit()

    client = _FakeClient(response={"result": {"rows": []}})
    results = sync_missing_realization_reports(
        db_session, store_id=store_id, client=client, backfill_months=3, today=date(2026, 9, 13)
    )

    # Would try 2026-08, 2026-07, 2026-06 — but 08 is already archived, so
    # only 07 and 06 should actually hit the client.
    assert client.calls == [(2026, 7), (2026, 6)]
    assert [(y, m) for y, m, _ in results] == [(2026, 7), (2026, 6)]
    assert all(o.fetched for _, _, o in results)
