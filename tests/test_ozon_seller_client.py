"""Tests for OzonSellerClient against a mocked HTTP layer — no real network
call. Regression coverage for a bug found on a real deployment: Ozon rejects
review/list with limit=1 ("value must be inside range [20, 100]"), so the
lightweight check_connection() probe must use a value Ozon actually accepts."""
import time
from unittest.mock import MagicMock

from app.services.ozon.client import OzonCredentials, OzonSellerClient
from app.services.ozon.exceptions import OzonRateLimited


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
    """get_product_queries()'s request/response contract is now CONFIRMED
    against a real account (see OzonSellerClient's module docstring): full
    ISO timestamps, and a `page_size` field rather than `limit`."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"whatever": "shape", "items": []}))

    result = client.get_product_queries(
        date_from="2026-08-08T00:00:00Z", date_to="2026-09-04T23:59:59Z", skus=["123"], page_size=10
    )

    assert result == {"whatever": "shape", "items": []}
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v1/analytics/product-queries"
    assert sent_kwargs["json"]["date_from"] == "2026-08-08T00:00:00Z"
    assert sent_kwargs["json"]["date_to"] == "2026-09-04T23:59:59Z"
    assert sent_kwargs["json"]["skus"] == ["123"]
    assert sent_kwargs["json"]["page_size"] == 10
    assert "limit" not in sent_kwargs["json"]
    assert "limit_by_sku" not in sent_kwargs["json"]


def test_get_product_query_details_sends_expected_path():
    """get_product_query_details() requires BOTH limit_by_sku and page_size
    in the same request (confirmed live: Ozon returns a validation error if
    either is missing) and has no `query` parameter at all — the response
    itself carries one row per query via its own `query` field."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"whatever": "shape"}))

    result = client.get_product_query_details(
        date_from="2026-08-08T00:00:00Z",
        date_to="2026-09-04T23:59:59Z",
        skus=["2953864771"],
        limit_by_sku=10,
        page_size=10,
    )

    assert result == {"whatever": "shape"}
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v1/analytics/product-queries/details"
    assert sent_kwargs["json"]["date_from"] == "2026-08-08T00:00:00Z"
    assert sent_kwargs["json"]["date_to"] == "2026-09-04T23:59:59Z"
    assert sent_kwargs["json"]["skus"] == ["2953864771"]
    assert sent_kwargs["json"]["limit_by_sku"] == 10
    assert sent_kwargs["json"]["page_size"] == 10
    assert "query" not in sent_kwargs["json"]


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


def test_get_analytics_data_sends_expected_path_and_body():
    """get_analytics_data()'s REQUEST contract is CONFIRMED straight from
    the official docs (see OzonSellerClient's module docstring) — plain
    "YYYY-MM-DD" dates (not the "...T00:00:00Z" timestamp product-queries
    needs), dimension/metrics as separate lists, filters defaulting to [].
    The RESPONSE shape is NOT confirmed, so this only pins the request."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"result": {"data": [], "totals": []}, "timestamp": "x"}))

    result = client.get_analytics_data(
        date_from="2026-08-08", date_to="2026-09-04", dimension=["sku", "day"], metrics=["revenue", "ordered_units"],
        limit=20,
    )

    assert result == {"result": {"data": [], "totals": []}, "timestamp": "x"}
    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v1/analytics/data"
    assert sent_kwargs["json"]["date_from"] == "2026-08-08"
    assert sent_kwargs["json"]["date_to"] == "2026-09-04"
    assert sent_kwargs["json"]["dimension"] == ["sku", "day"]
    assert sent_kwargs["json"]["metrics"] == ["revenue", "ordered_units"]
    assert sent_kwargs["json"]["limit"] == 20
    assert sent_kwargs["json"]["offset"] == 0
    assert sent_kwargs["json"]["filters"] == []
    assert "sort" not in sent_kwargs["json"]


def test_list_fbo_postings_sends_expected_path_and_body():
    """UNCONFIRMED against a real account (see OzonSellerClient's module
    docstring) — this only pins down the request shape this client sends
    and that a plausible response parses without crashing."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(
        return_value=_mock_post(200, {"result": {"postings": [{"posting_number": "123-0001-1"}], "has_next": False}})
    )

    result = client.list_fbo_postings(date_from="2026-08-08T00:00:00Z", date_to="2026-09-04T23:59:59Z")

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v2/posting/fbo/list"
    assert sent_kwargs["json"]["filter"] == {"since": "2026-08-08T00:00:00Z", "to": "2026-09-04T23:59:59Z"}
    assert result.result.postings[0].posting_number == "123-0001-1"


def test_list_fbs_postings_sends_expected_path_and_body():
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"result": {"postings": [], "has_next": False}}))

    client.list_fbs_postings(date_from="2026-08-08T00:00:00Z", date_to="2026-09-04T23:59:59Z")

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v3/posting/fbs/list"
    assert sent_kwargs["json"]["filter"] == {"since": "2026-08-08T00:00:00Z", "to": "2026-09-04T23:59:59Z"}


def test_list_fbo_postings_tolerates_an_unexpected_response_shape():
    """The whole point of extra="allow" + all-optional fields on
    OzonPostingListResponse: a response shape this client's author didn't
    anticipate must not raise a validation error — it should just come back
    with empty/None fields rather than crash the caller."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(200, {"totally": "unexpected", "shape": 123}))

    result = client.list_fbo_postings(date_from="2026-08-08T00:00:00Z", date_to="2026-09-04T23:59:59Z")

    assert result.result is None


def test_list_finance_transactions_sends_expected_path_and_body():
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(
        return_value=_mock_post(200, {"result": {"operations": [{"operation_id": 1, "amount": -150.5}], "page_count": 1}})
    )

    result = client.list_finance_transactions(date_from="2026-08-08T00:00:00Z", date_to="2026-09-04T23:59:59Z")

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v3/finance/transaction/list"
    assert sent_kwargs["json"]["filter"] == {
        "date": {"from": "2026-08-08T00:00:00Z", "to": "2026-09-04T23:59:59Z"}, "transaction_type": "all",
    }
    assert result.result.operations[0].amount == -150.5


def test_post_retries_on_429_and_succeeds_once_ozon_stops_limiting(monkeypatch):
    """Regression test for a real production finding: the search-query-
    details sync's per-SKU fallback (see
    app.services.search_query_details_sync_service) fires many individual
    requests in a row when a grouped batch comes back empty, and Ozon starts
    returning 429 mid-burst. _post()'s own retry (tenacity, exponential
    backoff) must transparently ride this out rather than surface a 429 to
    the caller on the first hit."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # skip the real 1/2/4/8s waits in tests
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(
        side_effect=[
            _mock_post(429, {"message": "too many requests"}),
            _mock_post(429, {"message": "too many requests"}),
            _mock_post(200, {"reviews": [], "has_next": False}),
        ]
    )

    result = client.check_connection()

    assert result["ok"] is True
    assert client._client.post.call_count == 3


def test_post_gives_up_after_max_attempts_on_persistent_429(monkeypatch):
    """If Ozon keeps rate-limiting past every retry attempt, _post() must
    eventually give up and raise OzonRateLimited rather than retry forever —
    bumped from 3 to 5 attempts (4 waits: 1, 2, 4, 8s) after 3 was confirmed
    too impatient live for the per-SKU fallback's request burst."""
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(429, {"message": "too many requests"}))

    try:
        client._post("/v1/review/list", {"limit": 20})
        assert False, "expected OzonRateLimited"
    except OzonRateLimited:
        pass

    assert client._client.post.call_count == 5
