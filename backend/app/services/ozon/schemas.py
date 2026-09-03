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
