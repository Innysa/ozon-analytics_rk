"""Typed, tolerant wrappers around Ozon Seller API review payloads.

Ozon's review methods (`/v1/review/*`) are documented as beta and require a
Premium Plus subscription; exact field names can change. We therefore parse
defensively (`extra="allow"`, optional fields) and always keep the raw JSON
alongside the parsed view, rather than assuming a rigid contract. Before
relying on a new field in production, verify it against the current
documentation at https://docs.ozon.ru/api/seller/.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OzonReviewItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    sku: int | str | None = None
    text: str | None = None
    rating: int | None = None
    published_at: str | None = None
    status: str | None = None
    comments_amount: int | None = None
    photos_amount: int | None = None
    videos_amount: int | None = None
    order_status: str | None = None
    is_rating_participant: bool | None = None


class OzonReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    reviews: list[OzonReviewItem] = []
    has_next: bool | None = None
    last_id: str | None = None


class OzonReviewCommentItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    text: str | None = None
    is_owner: bool | None = None
    published_at: str | None = None


# --- Product/inventory (/v3/product/list, /v3/product/info/list) --------
#
# Field names and response shape verified against real payloads captured
# from a live store, not just the docs: /v3/product/list wraps its page in
# a top-level "result" object ({"result": {"items": [...], "total": N,
# "last_id": "..."}}), while /v3/product/info/list returns "items" at the
# top level with no such wrapper — the two are not symmetrical.


class OzonProductListItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    product_id: int
    offer_id: str | None = None
    sku: int | None = None
    has_fbo_stocks: bool | None = None
    has_fbs_stocks: bool | None = None
    archived: bool | None = None
    is_discounted: bool | None = None


class OzonProductListResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[OzonProductListItem] = []
    total: int | None = None
    last_id: str | None = None


class OzonProductListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: OzonProductListResult


class OzonProductStockItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str | None = None  # "fbo" or "fbs"
    sku: int | None = None
    present: int | None = None
    reserved: int | None = None


class OzonProductStocks(BaseModel):
    model_config = ConfigDict(extra="allow")

    has_stock: bool | None = None
    stocks: list[OzonProductStockItem] = []


class OzonProductInfoItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str | None = None
    offer_id: str | None = None
    sku: int | None = None
    is_archived: bool | None = None
    price: str | None = None
    old_price: str | None = None
    currency_code: str | None = None
    primary_image: list[str] = []
    stocks: OzonProductStocks | None = None


class OzonProductInfoListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: list[OzonProductInfoItem] = []


# --- Postings / orders (/v2/posting/fbo/list, /v3/posting/fbs/list) -----
# --- Finance transactions (/v3/finance/transaction/list) -----------------
#
# UNCONFIRMED — unlike everything above (verified against real captured
# payloads before being relied on), these have NOT been called against a
# real account. The request shape (filter/pagination fields) follows Ozon's
# published API documentation and is unlikely to be wrong — that part of
# the contract is stable across sellers. The RESPONSE shape below is
# deliberately maximally tolerant (every field optional, extra="allow")
# specifically because past mistakes this session (guessed CSV column
# names, guessed JSON keys) all happened on the response side, never the
# request side. Do NOT build a sync/scheduler/UI on top of these fields
# without first running backend/scripts/debug_orders_finance_api.py against
# a real store and confirming the actual field names/values it prints.


class OzonPostingProductItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    sku: int | None = None
    offer_id: str | None = None
    name: str | None = None
    quantity: int | None = None
    price: str | None = None


class OzonPostingItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    posting_number: str | None = None
    order_id: int | None = None
    order_number: str | None = None
    status: str | None = None
    in_process_at: str | None = None
    products: list[OzonPostingProductItem] = []
    financial_data: dict | None = None
    analytics_data: dict | None = None
    cancellation: dict | None = None


class OzonPostingListResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    postings: list[OzonPostingItem] = []
    has_next: bool | None = None


class OzonPostingListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: OzonPostingListResult | list[OzonPostingItem] | None = None


class OzonFinanceOperationItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    operation_id: int | None = None
    operation_type: str | None = None
    operation_type_name: str | None = None
    operation_date: str | None = None
    type: str | None = None
    amount: float | None = None
    accruals_for_sale: float | None = None
    sale_commission: float | None = None
    delivery_charge: float | None = None
    return_delivery_charge: float | None = None
    posting: dict | None = None
    items: list[dict] = []
    services: list[dict] = []


class OzonFinanceTransactionListResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    operations: list[OzonFinanceOperationItem] = []
    page_count: int | None = None
    row_count: int | None = None


class OzonFinanceTransactionListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    result: OzonFinanceTransactionListResult | None = None
