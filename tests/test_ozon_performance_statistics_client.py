"""Tests for OzonPerformanceClient's async statistics-report flow (create /
poll / download) against a mocked HTTP layer — no real network call. Covers
the "report busy" (max 1 concurrent report per account) error mapping, which
is the one behavior this client must get exactly right for the sequential
batch-sync worker to retry correctly instead of failing outright."""
from unittest.mock import MagicMock

import pytest

from app.services.ozon_performance.client import OzonPerformanceClient, PerformanceCredentials
from app.services.ozon_performance.exceptions import OzonPerformanceAuthError, OzonPerformanceReportBusy


def _mock_response(status_code: int, json_body: dict | None = None, content: bytes | None = None):
    response = MagicMock()
    response.status_code = status_code
    if json_body is not None:
        response.json.return_value = json_body
    response.text = str(json_body) if json_body is not None else ""
    if content is not None:
        response.content = content
    return response


def _client() -> OzonPerformanceClient:
    client = OzonPerformanceClient(PerformanceCredentials(client_id="cid", client_secret="secret"))
    client._access_token = "test-token"
    client._token_expires_at = float("inf")
    return client


def test_create_statistics_report_returns_uuid():
    client = _client()
    client._client.post = MagicMock(return_value=_mock_response(200, {"UUID": "abc-123", "vendor": False}))

    uuid = client.create_statistics_report(["1", "2"], "01.09.2026", "07.09.2026")

    assert uuid == "abc-123"
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/api/client/statistics"
    assert sent_kwargs["json"]["campaigns"] == ["1", "2"]
    assert sent_kwargs["json"]["dateFrom"] == "01.09.2026"
    assert sent_kwargs["json"]["dateTo"] == "07.09.2026"
    assert sent_kwargs["json"]["groupBy"] == "DATE"


def test_create_statistics_report_raises_busy_on_confirmed_error_text():
    """Real error text confirmed against a real account: "Превышен лимит
    активных запросов (максимум 1)" — must map to OzonPerformanceReportBusy
    specifically, not a generic API error, so the sync worker knows to
    wait-and-retry rather than abandon the whole sync."""
    client = _client()
    client._client.post = MagicMock(
        return_value=_mock_response(400, {"message": "Превышен лимит активных запросов (максимум 1)"})
    )

    with pytest.raises(OzonPerformanceReportBusy):
        client.create_statistics_report(["1"], "01.09.2026", "07.09.2026")


def test_create_statistics_report_auth_error():
    client = _client()
    client._client.post = MagicMock(return_value=_mock_response(401, {"message": "unauthorized"}))

    with pytest.raises(OzonPerformanceAuthError):
        client.create_statistics_report(["1"], "01.09.2026", "07.09.2026")


def test_get_statistics_report_status_in_progress():
    client = _client()
    client._client.get = MagicMock(return_value=_mock_response(200, {"state": "IN_PROGRESS"}))

    status = client.get_statistics_report_status("abc-123")

    assert status.state == "IN_PROGRESS"
    assert status.link is None


def test_get_statistics_report_status_ok_with_link():
    client = _client()
    client._client.get = MagicMock(
        return_value=_mock_response(200, {"state": "OK", "link": "/api/client/statistics/report?UUID=abc-123"})
    )

    status = client.get_statistics_report_status("abc-123")

    assert status.state == "OK"
    assert status.link == "/api/client/statistics/report?UUID=abc-123"


def test_download_statistics_report_returns_raw_bytes():
    client = _client()
    client._client.get = MagicMock(return_value=_mock_response(200, content=b"PK\x03\x04fake-zip-bytes"))

    content = client.download_statistics_report("/api/client/statistics/report?UUID=abc-123")

    assert content == b"PK\x03\x04fake-zip-bytes"
    sent_path, sent_kwargs = client._client.get.call_args
    assert sent_path[0] == "/api/client/statistics/report?UUID=abc-123"
