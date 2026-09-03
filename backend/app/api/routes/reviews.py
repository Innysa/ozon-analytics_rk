import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.core.encryption import decrypt_secret
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.ozon_credentials import OzonCredentials
from app.models.product import Product
from app.models.review import Review, ReviewStatus
from app.models.review_ai_analysis import ReviewAIAnalysis
from app.models.review_comment import CommentStatus, ReviewComment
from app.models.store_ai_settings import StoreAISettings
from app.models.user import User
from app.models.ai_generation import AIGeneration
from app.schemas.review import (
    ReviewAIAnalysisOut,
    ReviewCommentOut,
    ReviewCommentUpdate,
    ReviewListResponse,
    ReviewOut,
    RewriteInstruction,
)
from app.services.audit import record_audit
from app.services.ai.factory import get_ai_provider
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient
from app.services.ozon.exceptions import OzonAPIError

router = APIRouter(prefix="/api/stores/{store_id}/reviews", tags=["reviews"])


def _analysis_out(analysis: ReviewAIAnalysis | None) -> ReviewAIAnalysisOut | None:
    if not analysis:
        return None
    return ReviewAIAnalysisOut(
        sentiment=analysis.sentiment,
        category=analysis.category,
        urgency=analysis.urgency,
        reply_needed=analysis.reply_needed,
        advantages=json.loads(analysis.advantages_json or "[]"),
        complaints=json.loads(analysis.complaints_json or "[]"),
        product_improvements=json.loads(analysis.product_improvements_json or "[]"),
        card_improvements=json.loads(analysis.card_improvements_json or "[]"),
        hypotheses=json.loads(analysis.hypotheses_json or "[]"),
    )


def _latest_comment(db: Session, review_id: str) -> ReviewComment | None:
    return db.scalars(
        select(ReviewComment).where(ReviewComment.review_id == review_id).order_by(ReviewComment.created_at.desc())
    ).first()


def _review_out(db: Session, review: Review, product: Product | None) -> ReviewOut:
    latest = _latest_comment(db, review.id)
    return ReviewOut(
        id=review.id,
        store_id=review.store_id,
        product_id=review.product_id,
        product_name=product.name if product else None,
        product_sku=product.ozon_sku if product else None,
        product_offer_id=product.offer_id if product else None,
        product_image_url=product.image_url if product else None,
        ozon_review_id=review.ozon_review_id,
        source=review.source,
        rating=review.rating,
        text=review.text,
        pros=review.pros,
        cons=review.cons,
        existing_seller_reply=review.existing_seller_reply,
        published_at=review.published_at,
        status=review.status,
        is_demo=review.is_demo,
        analysis=_analysis_out(review.ai_analysis),
        latest_draft=ReviewCommentOut.model_validate(latest) if latest else None,
    )


@router.get("", response_model=ReviewListResponse)
def list_reviews(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    rating: int | None = None,
    sentiment: str | None = None,
    category: str | None = None,
    statuses: str | None = None,  # comma-separated ReviewStatus values
    has_reply: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> ReviewListResponse:
    stmt = select(Review).where(Review.store_id == ctx.store_id)
    if product_id:
        stmt = stmt.where(Review.product_id == product_id)
    if rating:
        stmt = stmt.where(Review.rating == rating)
    if date_from:
        stmt = stmt.where(Review.published_at >= date_from)
    if date_to:
        stmt = stmt.where(Review.published_at <= date_to)
    if statuses:
        wanted = [ReviewStatus(s) for s in statuses.split(",") if s]
        stmt = stmt.where(Review.status.in_(wanted))

    reviews = db.scalars(stmt.order_by(Review.published_at.desc().nulls_last(), Review.created_at.desc())).all()

    product_ids = {r.product_id for r in reviews if r.product_id}
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()} if product_ids else {}

    items = []
    for r in reviews:
        if sentiment and (not r.ai_analysis or r.ai_analysis.sentiment != sentiment):
            continue
        if category and (not r.ai_analysis or r.ai_analysis.category != category):
            continue
        has_any_reply = bool(r.existing_seller_reply) or r.status in (ReviewStatus.PUBLISHED,)
        if has_reply is not None and has_any_reply != has_reply:
            continue
        items.append(_review_out(db, r, products.get(r.product_id)))

    return ReviewListResponse(items=items, total=len(items))


@router.get("/{review_id}", response_model=ReviewOut)
def get_review(review_id: str, ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)) -> ReviewOut:
    review = db.get(Review, review_id)
    if not review or review.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отзыв не найден")
    product = db.get(Product, review.product_id) if review.product_id else None
    return _review_out(db, review, product)


def _get_review_or_404(db: Session, ctx: StoreContext, review_id: str) -> Review:
    review = db.get(Review, review_id)
    if not review or review.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отзыв не найден")
    return review


def _log_ai_generation(db: Session, *, store_id: str, review_id: str, user_id: str, operation: str, outcome) -> None:
    db.add(
        AIGeneration(
            store_id=store_id,
            review_id=review_id,
            user_id=user_id,
            provider=get_ai_provider().name,
            operation=operation,
            model=outcome.usage.model,
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            latency_ms=outcome.usage.latency_ms,
            estimated_cost_rub=outcome.usage.estimated_cost_rub,
            success=outcome.success,
            error_message=outcome.error_message,
        )
    )


@router.post("/{review_id}/analyze", response_model=ReviewOut)
def analyze_review(
    review_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewOut:
    review = _get_review_or_404(db, ctx, review_id)
    product = db.get(Product, review.product_id) if review.product_id else None
    ai_settings = db.query(StoreAISettings).filter(StoreAISettings.store_id == ctx.store_id).first()

    provider = get_ai_provider()
    outcome = provider.analyze_review(
        product_name=product.name if product else None,
        rating=review.rating,
        text=review.text,
        pros=review.pros,
        cons=review.cons,
        store_settings=ai_settings,
    )
    _log_ai_generation(db, store_id=ctx.store_id, review_id=review.id, user_id=user.id, operation="analyze_review", outcome=outcome)

    if not outcome.success or not outcome.result:
        record_audit(db, action="review_analyzed", user_id=user.id, store_id=ctx.store_id, target_type="review", target_id=review.id, result="failure", message=outcome.error_message)
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Не удалось проанализировать отзыв: {outcome.error_message}")

    result = outcome.result
    existing = db.query(ReviewAIAnalysis).filter(ReviewAIAnalysis.review_id == review.id).first()
    if not existing:
        existing = ReviewAIAnalysis(review_id=review.id, store_id=ctx.store_id, sentiment="", category="", urgency="", reply_needed=True)
        db.add(existing)

    existing.sentiment = result.sentiment
    existing.category = result.category
    existing.urgency = result.urgency
    existing.reply_needed = result.reply_needed
    existing.advantages_json = json.dumps(result.advantages, ensure_ascii=False)
    existing.complaints_json = json.dumps(result.complaints, ensure_ascii=False)
    existing.product_improvements_json = json.dumps(result.product_improvements, ensure_ascii=False)
    existing.card_improvements_json = json.dumps(result.card_improvements, ensure_ascii=False)
    existing.hypotheses_json = json.dumps(result.hypotheses, ensure_ascii=False)
    existing.model_used = outcome.usage.model

    review.status = ReviewStatus.ANALYZED if result.reply_needed else ReviewStatus.NO_REPLY_NEEDED
    db.flush()
    record_audit(db, action="review_analyzed", user_id=user.id, store_id=ctx.store_id, target_type="review", target_id=review.id)
    db.commit()
    return _review_out(db, review, product)


def _create_draft_comment(db: Session, ctx: StoreContext, review: Review, text: str) -> ReviewComment:
    comment = ReviewComment(
        review_id=review.id,
        store_id=ctx.store_id,
        text=text,
        status=CommentStatus.DRAFT,
        generated_by_ai=True,
    )
    db.add(comment)
    review.status = ReviewStatus.DRAFT_CREATED
    db.flush()
    return comment


@router.post("/{review_id}/generate-draft", response_model=ReviewOut)
def generate_draft(
    review_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewOut:
    review = _get_review_or_404(db, ctx, review_id)
    product = db.get(Product, review.product_id) if review.product_id else None
    ai_settings = db.query(StoreAISettings).filter(StoreAISettings.store_id == ctx.store_id).first()

    provider = get_ai_provider()
    outcome = provider.generate_review_reply(
        product_name=product.name if product else None,
        rating=review.rating,
        text=review.text,
        pros=review.pros,
        cons=review.cons,
        store_settings=ai_settings,
    )
    _log_ai_generation(db, store_id=ctx.store_id, review_id=review.id, user_id=user.id, operation="generate_reply", outcome=outcome)

    if not outcome.success or not outcome.reply_text:
        record_audit(db, action="draft_created", user_id=user.id, store_id=ctx.store_id, target_type="review", target_id=review.id, result="failure", message=outcome.error_message)
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Не удалось сгенерировать ответ: {outcome.error_message}")

    _create_draft_comment(db, ctx, review, outcome.reply_text)
    record_audit(db, action="draft_created", user_id=user.id, store_id=ctx.store_id, target_type="review", target_id=review.id)
    db.commit()
    return _review_out(db, review, product)


@router.post("/{review_id}/comments/{comment_id}/rewrite", response_model=ReviewCommentOut)
def rewrite_comment(
    review_id: str,
    comment_id: str,
    payload: RewriteInstruction,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewCommentOut:
    review = _get_review_or_404(db, ctx, review_id)
    comment = db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Черновик не найден")
    if comment.status != CommentStatus.DRAFT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Переписать можно только черновик, ожидающий подтверждения")

    ai_settings = db.query(StoreAISettings).filter(StoreAISettings.store_id == ctx.store_id).first()
    provider = get_ai_provider()
    outcome = provider.rewrite_review_reply(existing_reply=comment.text, instruction=payload.instruction, store_settings=ai_settings)
    _log_ai_generation(db, store_id=ctx.store_id, review_id=review.id, user_id=user.id, operation=f"rewrite_{payload.instruction}", outcome=outcome)

    if not outcome.success or not outcome.reply_text:
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Не удалось переписать ответ: {outcome.error_message}")

    comment.text = outcome.reply_text
    db.flush()
    record_audit(db, action="reply_edited", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id, message=f"rewrite:{payload.instruction}")
    db.commit()
    return ReviewCommentOut.model_validate(comment)


@router.patch("/{review_id}/comments/{comment_id}", response_model=ReviewCommentOut)
def edit_comment(
    review_id: str,
    comment_id: str,
    payload: ReviewCommentUpdate,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewCommentOut:
    review = _get_review_or_404(db, ctx, review_id)
    comment = db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Черновик не найден")
    if comment.status not in (CommentStatus.DRAFT,):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Редактировать можно только черновик")

    comment.text = payload.text
    comment.edited_by_user = True
    db.flush()
    record_audit(db, action="reply_edited", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id)
    db.commit()
    return ReviewCommentOut.model_validate(comment)


@router.post("/{review_id}/comments/{comment_id}/approve", response_model=ReviewCommentOut)
def approve_comment(
    review_id: str,
    comment_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewCommentOut:
    """The only gate that unlocks publishing — a human must explicitly approve
    the (possibly AI-generated, possibly edited) draft first."""
    review = _get_review_or_404(db, ctx, review_id)
    comment = db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Черновик не найден")
    if comment.status != CommentStatus.DRAFT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Подтвердить можно только черновик")

    comment.status = CommentStatus.APPROVED
    comment.approved_by_user_id = user.id
    comment.approved_at = datetime.utcnow().isoformat()
    review.status = ReviewStatus.APPROVED
    db.flush()
    record_audit(db, action="reply_approved", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id)
    db.commit()
    return ReviewCommentOut.model_validate(comment)


@router.post("/{review_id}/comments/{comment_id}/publish", response_model=ReviewCommentOut)
def publish_comment(
    review_id: str,
    comment_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewCommentOut:
    review = _get_review_or_404(db, ctx, review_id)
    comment = db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Черновик не найден")
    if comment.status != CommentStatus.APPROVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Публиковать можно только одобренный ответ")

    creds = db.query(OzonCredentials).filter(OzonCredentials.store_id == ctx.store_id).first()
    if not creds or creds.reviews_api_available is False:
        comment.publish_error = "Публикация через API недоступна для этого магазина. Скопируйте ответ для ручной публикации."
        db.flush()
        record_audit(db, action="reply_publish_attempt", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id, result="failure", message=comment.publish_error)
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=comment.publish_error)

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)
    try:
        with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
            response = client.create_comment(review.ozon_review_id, comment.text)
        comment.status = CommentStatus.PUBLISHED
        comment.published_via = "ozon_api"
        comment.ozon_comment_id = str(response.get("comment_id") or response.get("id") or "")
        comment.publish_error = None
        review.status = ReviewStatus.PUBLISHED
        db.flush()
        record_audit(db, action="reply_published", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id, result="success")
        db.commit()
        return ReviewCommentOut.model_validate(comment)
    except OzonAPIError as exc:
        comment.status = CommentStatus.PUBLISH_FAILED
        comment.publish_error = str(exc)
        review.status = ReviewStatus.PUBLISH_FAILED
        db.flush()
        record_audit(db, action="reply_published", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id, result="failure", message=str(exc))
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Не удалось опубликовать ответ в Ozon: {exc}. Ответ можно скопировать вручную.")


@router.post("/{review_id}/comments/{comment_id}/copy", response_model=ReviewCommentOut)
def copy_comment(
    review_id: str,
    comment_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewCommentOut:
    review = _get_review_or_404(db, ctx, review_id)
    comment = db.get(ReviewComment, comment_id)
    if not comment or comment.review_id != review.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Черновик не найден")
    record_audit(db, action="reply_copied", user_id=user.id, store_id=ctx.store_id, target_type="review_comment", target_id=comment.id)
    db.commit()
    return ReviewCommentOut.model_validate(comment)


@router.post("/{review_id}/no-reply-needed", response_model=ReviewOut)
def mark_no_reply_needed(
    review_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewOut:
    review = _get_review_or_404(db, ctx, review_id)
    review.status = ReviewStatus.NO_REPLY_NEEDED
    db.flush()
    record_audit(db, action="review_marked_no_reply", user_id=user.id, store_id=ctx.store_id, target_type="review", target_id=review.id)
    db.commit()
    product = db.get(Product, review.product_id) if review.product_id else None
    return _review_out(db, review, product)


@router.post("/bulk/generate-drafts")
def bulk_generate_drafts(
    review_ids: list[str],
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Bulk draft generation is allowed; bulk *publishing* is intentionally not
    exposed anywhere in the API — every publish requires its own approval."""
    ai_settings = db.query(StoreAISettings).filter(StoreAISettings.store_id == ctx.store_id).first()
    provider = get_ai_provider()
    succeeded, failed = 0, 0
    for review_id in review_ids:
        review = db.get(Review, review_id)
        if not review or review.store_id != ctx.store_id:
            failed += 1
            continue
        product = db.get(Product, review.product_id) if review.product_id else None
        outcome = provider.generate_review_reply(
            product_name=product.name if product else None,
            rating=review.rating, text=review.text, pros=review.pros, cons=review.cons,
            store_settings=ai_settings,
        )
        _log_ai_generation(db, store_id=ctx.store_id, review_id=review.id, user_id=user.id, operation="generate_reply_bulk", outcome=outcome)
        if outcome.success and outcome.reply_text:
            _create_draft_comment(db, ctx, review, outcome.reply_text)
            succeeded += 1
        else:
            failed += 1
    record_audit(db, action="bulk_draft_generated", user_id=user.id, store_id=ctx.store_id, message=f"succeeded={succeeded} failed={failed}")
    db.commit()
    return {"succeeded": succeeded, "failed": failed}
