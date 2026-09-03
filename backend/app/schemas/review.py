from datetime import datetime

from pydantic import BaseModel

from app.models.review import ReviewSource, ReviewStatus
from app.models.review_comment import CommentStatus


class ReviewAIAnalysisOut(BaseModel):
    sentiment: str
    category: str
    urgency: str
    reply_needed: bool
    advantages: list[str] = []
    complaints: list[str] = []
    product_improvements: list[str] = []
    card_improvements: list[str] = []
    hypotheses: list[str] = []

    model_config = {"from_attributes": True}


class ReviewCommentOut(BaseModel):
    id: str
    text: str
    status: CommentStatus
    generated_by_ai: bool
    edited_by_user: bool
    published_via: str | None = None
    ozon_comment_id: str | None = None
    publish_error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewOut(BaseModel):
    id: str
    store_id: str
    product_id: str | None
    product_name: str | None = None
    product_sku: str | None = None
    product_offer_id: str | None = None
    product_image_url: str | None = None

    ozon_review_id: str
    source: ReviewSource
    rating: int
    text: str | None
    pros: str | None
    cons: str | None
    existing_seller_reply: str | None
    published_at: datetime | None
    status: ReviewStatus
    is_demo: bool

    analysis: ReviewAIAnalysisOut | None = None
    latest_draft: ReviewCommentOut | None = None

    model_config = {"from_attributes": True}


class ReviewListResponse(BaseModel):
    items: list[ReviewOut]
    total: int


class ReviewCommentUpdate(BaseModel):
    text: str


class RewriteInstruction(BaseModel):
    instruction: str  # shorter | warmer | formal | regenerate
