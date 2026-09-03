from pydantic import BaseModel


class RatingBucket(BaseModel):
    rating: int
    count: int


class NamedCount(BaseModel):
    label: str
    count: int
    review_ids: list[str] = []


class ReviewAnalyticsOut(BaseModel):
    has_data: bool
    total_reviews: int = 0
    average_rating: float | None = None
    low_rating_share: float | None = None  # доля оценок 1-3
    reviews_without_reply: int = 0
    rating_distribution: list[RatingBucket] = []

    top_advantages: list[NamedCount] = []
    top_complaints: list[NamedCount] = []

    products_with_rising_negativity: list[str] = []

    card_improvement_ideas: list[NamedCount] = []
    product_improvement_ideas: list[NamedCount] = []
    infographic_ideas: list[NamedCount] = []
