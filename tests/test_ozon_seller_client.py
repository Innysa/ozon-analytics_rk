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


def test_get_product_queries_sends_expected_path_and_returns_raw_dict():
    """get_product_queries()'s request/response contract is UNCONFIRMED (see
    OzonSellerClient's module docstring — docs.ozon.ru and api-seller.ozon.ru
    itself are both blocked from this sandbox). This only locks in the one
    thing that IS certain: the endpoint path, and that the raw dict is
    returned unmodified rather than forced through a guessed schema."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"whatever": "shape", "items": []}))

    result = client.get_product_queries(date_from="2026-08-01", date_to="2026-09-06", skus=["123"])

    assert result == {"whatever": "shape", "items": []}
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v1/analytics/product-queries"
    assert sent_kwargs["json"]["date_from"] == "2026-08-01"
    assert sent_kwargs["json"]["date_to"] == "2026-09-06"
    assert sent_kwargs["json"]["skus"] == ["123"]


def test_get_product_query_details_sends_expected_path():
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"whatever": "shape"}))

    result = client.get_product_query_details(query="обувница", date_from="2026-08-01", date_to="2026-09-06")

    assert result == {"whatever": "shape"}
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v1/analytics/product-queries/details"
    assert sent_kwargs["json"]["query"] == "обувница"


def test_get_product_queries_reports_feature_unavailable_on_404():
    """Ozon 404s beta/plan-gated methods for stores without the required
    subscription — the same behavior already confirmed for review/list and
    product/list on this client; product-queries is documented as requiring
    Premium/Premium Plus for full data, so the same mapping is assumed here
    even though the exact status code for a missing subscription on THIS
    endpoint is itself unconfirmed."""
    from app.services.ozon.exceptions import OzonFeatureUnavailable

    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(404, {"message": "not found"}))

    try:
        client.get_product_queries(date_from="2026-08-01", date_to="2026-09-06")
        assert False, "expected OzonFeatureUnavailable"
    except OzonFeatureUnavailable:
        pass
