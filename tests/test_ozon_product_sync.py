"""Tests for the Ozon product/inventory schemas and client methods used by
the /sync/ozon-products endpoint. Fixtures are trimmed excerpts of real
payloads captured from a live store — /v3/product/list wraps its page in a
top-level "result" object while /v3/product/info/list does not, which is
easy to get backwards without a live payload to check against."""
from contextlib import contextmanager
from unittest.mock import MagicMock

from tests.conftest import login

from app.services.ozon.client import OzonCredentials, OzonSellerClient
from app.services.ozon.schemas import OzonProductInfoListResponse, OzonProductListResponse

PRODUCT_LIST_PAYLOAD = {
    "result": {
        "items": [
            {
                "product_id": 957538170,
                "offer_id": "ковер/мех/сер/200",
                "has_fbo_stocks": True,
                "has_fbs_stocks": True,
                "archived": False,
                "is_discounted": False,
                "quants": [],
                "sku": 1492106823,
            },
            {
                "product_id": 969279265,
                "offer_id": "ковер/ванна/сер/1U",
                "has_fbo_stocks": False,
                "has_fbs_stocks": False,
                "archived": False,
                "is_discounted": False,
                "quants": [],
                "sku": 1501997508,
            },
        ],
        "total": 152,
        "last_id": "WzUzMzA1MDg3MDIsNTMzMDUwODcwMl0=",
    }
}

PRODUCT_INFO_PAYLOAD = {
    "items": [
        {
            "id": 5330508572,
            "name": "деталь чемодана",
            "offer_id": "чем27",
            "is_archived": False,
            "price": "20.00",
            "old_price": "100.00",
            "currency_code": "RUB",
            "primary_image": ["https://ir.ozone.ru/s3/multimedia-1-w/12115017932.jpg"],
            "stocks": {
                "has_stock": False,
                "stocks": [{"present": 0, "reserved": 0, "sku": 4936624632, "source": "fbs"}],
            },
            "sku": 4936624632,
        }
    ]
}


def _mock_post(json_body: dict):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def test_product_list_response_parses_nested_result_wrapper():
    parsed = OzonProductListResponse.model_validate(PRODUCT_LIST_PAYLOAD)

    assert parsed.result.total == 152
    assert parsed.result.last_id == "WzUzMzA1MDg3MDIsNTMzMDUwODcwMl0="
    assert len(parsed.result.items) == 2
    assert parsed.result.items[0].product_id == 957538170
    assert parsed.result.items[0].sku == 1492106823
    assert parsed.result.items[0].has_fbo_stocks is True
    assert parsed.result.items[1].has_fbs_stocks is False


def test_product_info_response_has_no_result_wrapper():
    """Unlike /v3/product/list, /v3/product/info/list returns items at the
    top level — a schema that expected a "result" key here would silently
    parse to an empty item list instead of failing loudly."""
    parsed = OzonProductInfoListResponse.model_validate(PRODUCT_INFO_PAYLOAD)

    assert len(parsed.items) == 1
    item = parsed.items[0]
    assert item.id == 5330508572
    assert item.sku == 4936624632
    assert item.offer_id == "чем27"
    assert item.price == "20.00"
    assert item.old_price == "100.00"
    assert item.primary_image == ["https://ir.ozone.ru/s3/multimedia-1-w/12115017932.jpg"]
    assert item.stocks is not None
    assert item.stocks.stocks[0].source == "fbs"
    assert item.stocks.stocks[0].present == 0


def test_list_products_sends_last_id_and_parses_result():
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(PRODUCT_LIST_PAYLOAD))

    page = client.list_products(last_id="prev-cursor")

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v3/product/list"
    assert sent_kwargs["json"]["last_id"] == "prev-cursor"
    assert page.result.total == 152


def test_get_products_info_sends_requested_ids():
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(PRODUCT_INFO_PAYLOAD))

    info = client.get_products_info([5330508572])

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v3/product/info/list"
    assert sent_kwargs["json"]["product_id"] == [5330508572]
    assert info.items[0].name == "деталь чемодана"


def test_get_products_info_by_offer_id_sends_requested_offer_ids():
    """Same endpoint as get_products_info(), filtered by offer_id — used as
    a fallback for products Ozon reports as sku=0 when queried by
    product_id (see sync_ozon_products)."""
    client = OzonSellerClient(OzonCredentials(client_id="cid", api_key="key"))
    client._client.post = MagicMock(return_value=_mock_post(PRODUCT_INFO_PAYLOAD))

    info = client.get_products_info_by_offer_id(["чем27"])

    sent_path, sent_kwargs = client._client.post.call_args
    assert sent_path[0] == "/v3/product/info/list"
    assert sent_kwargs["json"]["offer_id"] == ["чем27"]
    assert "product_id" not in sent_kwargs["json"]
    assert info.items[0].name == "деталь чемодана"


def test_sync_ozon_products_paginates_and_upserts_from_info(client, two_stores_with_users, monkeypatch):
    """End-to-end: /sync/ozon-products should page through /v3/product/list
    until last_id stops advancing, then fetch details for every collected id
    via /v3/product/info/list and upsert a Product row per item, aggregating
    stocks by source."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    put_resp = client.put(
        f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"}
    )
    assert put_resp.status_code == 200

    list_response = OzonProductListResponse.model_validate(PRODUCT_LIST_PAYLOAD)
    empty_response = OzonProductListResponse.model_validate({"result": {"items": [], "total": 0, "last_id": ""}})
    info_response = OzonProductInfoListResponse.model_validate(PRODUCT_INFO_PAYLOAD)

    calls = {"list_products": 0}

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()

        def _list_products(*, last_id=""):
            calls["list_products"] += 1
            return empty_response if last_id else list_response

        fake.list_products.side_effect = _list_products
        fake.get_products_info.return_value = info_response
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["items_created"] == 1  # info_response only has one item

    # Paged until last_id stopped advancing (initial call + one follow-up).
    assert calls["list_products"] == 2

    products_resp = client.get(f"/api/stores/{d['store_a'].id}/products")
    assert products_resp.status_code == 200
    products = products_resp.json()
    assert len(products) == 1
    product = products[0]
    assert product["ozon_sku"] == "4936624632"
    assert product["offer_id"] == "чем27"
    assert float(product["price_rub"]) == 20.0
    assert float(product["old_price_rub"]) == 100.0
    assert product["fbs_stock"] == 0
    assert product["fbo_stock"] == 0
    assert product["is_archived"] is False

    # Re-running the sync must update the existing row, not duplicate it.
    resp2 = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp2.status_code == 200
    assert resp2.json()["items_skipped_duplicate"] == 1
    products_resp2 = client.get(f"/api/stores/{d['store_a'].id}/products")
    assert len(products_resp2.json()) == 1


def test_stale_sku_zero_row_is_corrected_in_place_not_duplicated(client, two_stores_with_users, monkeypatch, db_session):
    """Regression test: a product row stuck at ozon_sku="0" (Ozon's own "no
    SKU assigned yet" sentinel — see the skip in sync_ozon_products) used to
    be left orphaned forever once Ozon later assigned it a real sku: the old
    sku-only lookup was keyed under the stale "0", so it never matched the
    new sku and a brand-new duplicate Product row was created instead.
    Matching by Ozon's own stable product_id first must find and correct the
    same row in place."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    client.put(f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"})

    from app.models.product import Product

    stale = Product(store_id=d["store_a"].id, ozon_sku="0", ozon_product_id=5330508572, name="Товар без SKU")
    db_session.add(stale)
    db_session.commit()

    list_payload = {
        "result": {
            "items": [
                {
                    "product_id": 5330508572, "offer_id": "чем27", "has_fbo_stocks": False,
                    "has_fbs_stocks": True, "archived": False, "is_discounted": False, "quants": [],
                    "sku": 4936624632,
                }
            ],
            "total": 1, "last_id": "",
        }
    }
    list_response = OzonProductListResponse.model_validate(list_payload)
    info_response = OzonProductInfoListResponse.model_validate(PRODUCT_INFO_PAYLOAD)  # product_id=5330508572, sku=4936624632

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()
        fake.list_products.return_value = list_response
        fake.get_products_info.return_value = info_response
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items_created"] == 0
    assert resp.json()["items_skipped_duplicate"] == 1  # the stale row was updated, not duplicated

    products = client.get(f"/api/stores/{d['store_a'].id}/products").json()
    assert len(products) == 1
    assert products[0]["ozon_sku"] == "4936624632"


def test_sku_zero_by_product_id_is_resolved_via_offer_id_fallback(client, two_stores_with_users, monkeypatch):
    """End-to-end regression test for the reported real-world case: a "нет
    на складе" product (not yet delivered to an Ozon warehouse) comes back
    with sku=0 when /v3/product/info/list is queried by product_id, even
    though Ozon has already assigned it a real SKU (visible in Ozon's own
    "Аналитика" section). Re-querying the same endpoint by offer_id must
    resolve the real sku instead of the product being skipped."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    client.put(f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"})

    offer_id = "мус/вед/бел1/3"
    real_sku = 5716615794

    list_payload = {
        "result": {
            "items": [
                {
                    "product_id": 111222333, "offer_id": offer_id, "has_fbo_stocks": False,
                    "has_fbs_stocks": False, "archived": False, "is_discounted": False, "quants": [],
                    "sku": 0,
                }
            ],
            "total": 1, "last_id": "",
        }
    }
    zero_sku_info_payload = {
        "items": [
            {
                "id": 111222333, "name": "Мусорное ведро для кухни и туалета с крышкой 11 л",
                "offer_id": offer_id, "is_archived": False, "price": "990.00", "old_price": "1200.00",
                "currency_code": "RUB", "primary_image": [], "stocks": {"has_stock": False, "stocks": []},
                "sku": 0,
            }
        ]
    }
    resolved_info_payload = {
        "items": [
            {
                "id": 111222333, "name": "Мусорное ведро для кухни и туалета с крышкой 11 л",
                "offer_id": offer_id, "is_archived": False, "price": "990.00", "old_price": "1200.00",
                "currency_code": "RUB", "primary_image": [], "stocks": {"has_stock": False, "stocks": []},
                "sku": real_sku,
            }
        ]
    }

    list_response = OzonProductListResponse.model_validate(list_payload)
    zero_sku_info_response = OzonProductInfoListResponse.model_validate(zero_sku_info_payload)
    resolved_info_response = OzonProductInfoListResponse.model_validate(resolved_info_payload)

    calls = {"get_products_info": 0, "get_products_info_by_offer_id": 0}

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()
        fake.list_products.return_value = list_response

        def _get_products_info(product_ids):
            calls["get_products_info"] += 1
            return zero_sku_info_response

        def _get_products_info_by_offer_id(offer_ids):
            calls["get_products_info_by_offer_id"] += 1
            assert offer_ids == [offer_id]  # only the zero-sku subset is retried
            return resolved_info_response

        fake.get_products_info.side_effect = _get_products_info
        fake.get_products_info_by_offer_id.side_effect = _get_products_info_by_offer_id
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items_created"] == 1
    assert calls["get_products_info_by_offer_id"] == 1

    products = client.get(f"/api/stores/{d['store_a'].id}/products").json()
    assert len(products) == 1
    assert products[0]["ozon_sku"] == str(real_sku)
    assert products[0]["offer_id"] == offer_id
    assert products[0]["name"] == "Мусорное ведро для кухни и туалета с крышкой 11 л"


def test_sku_still_zero_after_offer_id_fallback_is_skipped(client, two_stores_with_users, monkeypatch):
    """If the offer_id fallback still comes back with sku=0/missing, the
    product must genuinely be skipped (no row created) rather than stored
    with an invalid sku."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    client.put(f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"})

    offer_id = "truly-unassigned"
    list_payload = {
        "result": {
            "items": [
                {
                    "product_id": 999, "offer_id": offer_id, "has_fbo_stocks": False,
                    "has_fbs_stocks": False, "archived": False, "is_discounted": False, "quants": [],
                    "sku": 0,
                }
            ],
            "total": 1, "last_id": "",
        }
    }
    zero_info_payload = {
        "items": [
            {
                "id": 999, "name": "Товар без SKU", "offer_id": offer_id, "is_archived": False,
                "price": None, "old_price": None, "currency_code": "RUB", "primary_image": [],
                "stocks": None, "sku": 0,
            }
        ]
    }

    list_response = OzonProductListResponse.model_validate(list_payload)
    zero_info_response = OzonProductInfoListResponse.model_validate(zero_info_payload)

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()
        fake.list_products.return_value = list_response
        fake.get_products_info.return_value = zero_info_response
        fake.get_products_info_by_offer_id.return_value = zero_info_response  # still sku=0
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    assert resp.json()["items_created"] == 0

    products = client.get(f"/api/stores/{d['store_a'].id}/products").json()
    assert len(products) == 0


def test_duplicate_rows_for_same_product_are_merged_on_conflict(client, two_stores_with_users, monkeypatch, db_session):
    """Regression test for a real production crash: a stale ozon_sku="0"
    placeholder row (matched by product_id) and a SEPARATE row that already
    holds the real sku (matched by sku) can both exist for the same product —
    the historic sku=0 duplicate-row bug (see app.services.product_merge's
    module docstring). Correcting the placeholder's sku in place used to
    collide with the other row under uq_product_store_sku, crashing with
    psycopg.errors.UniqueViolation (confirmed live for offer_id
    "мус/вед/бел1/3", real sku 5716615794). Syncing must merge the two rows
    instead — including reassigning the placeholder's own related data (e.g.
    a review) onto the surviving row — not crash."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    client.put(f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"})

    from app.models.product import Product
    from app.models.review import Review, ReviewSource, ReviewStatus

    offer_id = "мус/вед/бел1/3"
    real_sku = 5716615794
    product_id = 111222333

    placeholder = Product(
        store_id=d["store_a"].id, ozon_sku="0", ozon_product_id=product_id,
        offer_id=offer_id, name="Мусорное ведро (плейсхолдер)",
    )
    real_row = Product(
        store_id=d["store_a"].id, ozon_sku=str(real_sku),
        name="Мусорное ведро для кухни и туалета с крышкой 11 л",
    )
    db_session.add_all([placeholder, real_row])
    db_session.flush()
    # Attached to the LOSING row (invalid sku always loses — see
    # pick_survivor) to prove the merge reassigns related data rather than
    # discarding it when the placeholder happens to carry history.
    review = Review(
        store_id=d["store_a"].id, product_id=placeholder.id, ozon_review_id="rev-1",
        source=ReviewSource.OZON_API, rating=5, status=ReviewStatus.NEW,
    )
    db_session.add(review)
    db_session.commit()

    list_payload = {
        "result": {
            "items": [
                {
                    "product_id": product_id, "offer_id": offer_id, "has_fbo_stocks": False,
                    "has_fbs_stocks": False, "archived": False, "is_discounted": False, "quants": [],
                    "sku": real_sku,
                }
            ],
            "total": 1, "last_id": "",
        }
    }
    info_payload = {
        "items": [
            {
                "id": product_id, "name": "Мусорное ведро для кухни и туалета с крышкой 11 л",
                "offer_id": offer_id, "is_archived": False, "price": "990.00", "old_price": "1200.00",
                "currency_code": "RUB", "primary_image": [], "stocks": {"has_stock": False, "stocks": []},
                "sku": real_sku,
            }
        ]
    }
    list_response = OzonProductListResponse.model_validate(list_payload)
    info_response = OzonProductInfoListResponse.model_validate(info_payload)

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()
        fake.list_products.return_value = list_response
        fake.get_products_info.return_value = info_response
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"  # must not crash or get stuck running

    products = client.get(f"/api/stores/{d['store_a'].id}/products").json()
    assert len(products) == 1  # the two duplicate rows were merged into one
    assert products[0]["ozon_sku"] == str(real_sku)
    surviving_id = products[0]["id"]

    db_session.expire_all()
    assert db_session.query(Product).count() == 1
    assert db_session.query(Review).filter(Review.ozon_review_id == "rev-1").one().product_id == surviving_id


def test_final_commit_failure_leaves_run_failed_not_stuck_running(client, two_stores_with_users, monkeypatch, db_session):
    """Regression test for the other half of the reported production bug:
    a SyncRun must never be left at status="running" forever, and no
    PendingRollbackError should leak out of the request, if persisting the
    run's final status fails for any reason (this used to happen for
    exactly the uq_product_store_sku violation the other tests in this
    module now prevent at the source, but this test covers the finalize
    step's own safety net regardless of cause)."""
    d = two_stores_with_users
    # Setting Ozon Seller API credentials is now restricted to the platform
    # admin (see require_platform_admin_for_store) — a store's own "owner"
    # role no longer suffices.
    login(client, "admin@example.com", "adminpass123")
    client.put(f"/api/stores/{d['store_a'].id}/ozon/credentials", json={"client_id": "cid", "api_key": "key"})

    empty_list_response = OzonProductListResponse.model_validate({"result": {"items": [], "total": 0, "last_id": ""}})

    @contextmanager
    def fake_client_cm(*_args, **_kwargs):
        fake = MagicMock()
        fake.list_products.return_value = empty_list_response
        yield fake

    import app.api.routes.sync as sync_module

    monkeypatch.setattr(sync_module, "OzonSellerClient", fake_client_cm)

    real_commit = db_session.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:  # 1st = the "sync_started" commit; this is the finalize commit
            raise Exception("duplicate key value violates unique constraint \"uq_product_store_sku\"")
        return real_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)

    resp = client.post(f"/api/stores/{d['store_a'].id}/sync/ozon-products")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    assert "результат синхронизации" in body["error_message"]

    runs = client.get(f"/api/stores/{d['store_a'].id}/sync/runs").json()
    assert runs[0]["status"] == "failed"  # not stuck "running"
