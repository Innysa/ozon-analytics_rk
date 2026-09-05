"""Tests for OzonSellerClient against a mocked HTTP layer — no real network
call. Regression coverage for a bug found on a real deployment: Ozon rejects
review/list with limit=1 ("value must be inside range [20, 100]"), so the
lightweight check_connection() probe must use a value Ozon actually accepts."""
from unittest.mock import MagicMock

from app.services.ozon.client import OzonCredentials, OzonSellerClient


def _mock_post(status_code: int, json_body: dict):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def test_check_connection_sends_a_limit_ozon_actually_accepts():
    """Ozon's real API rejects limit values outside [20, 100] — confirmed live
    on a real deployment with the exact error 'Request validation error:
    invalid ReviewListRequest.Limit: value must be inside range [20, 100]'."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"reviews": [], "has_next": False}))

    result = client.check_connection()

    assert result["ok"] is True
    sent_path, sent_kwargs = client._client.post.call_args
    sent_limit = sent_kwargs["json"]["limit"]
    assert 20 <= sent_limit <= 100, f"limit={sent_limit} is outside Ozon's accepted [20, 100] range"


def test_check_connection_reports_auth_error(monkeypatch):
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(401, {"message": "unauthorized"}))

    result = client.check_connection()

    assert result["ok"] is False
    assert "Client-Id" in result["message"] or "Api-Key" in result["message"]
