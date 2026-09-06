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
