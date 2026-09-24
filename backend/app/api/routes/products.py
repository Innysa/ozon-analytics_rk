import json
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.product_card_ai_review import ProductCardAiReview
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.common import ImportSummary
from app.schemas.product import (
    ProductCardAiReviewListResponse,
    ProductCardAiReviewOut,
    ProductCostPriceIn,
    ProductOut,
)
from app.services.ai.factory import get_ai_provider
from app.services.audit import record_audit
from app.services.product_card_ai_review_service import generate_product_card_ai_review
from app.services.product_cost_price_import import import_product_cost_price_from_file
from app.services.product_localization_import import import_product_localization_from_file

router = APIRouter(prefix="/api/stores/{store_id}/products", tags=["products"])


@router.get("", response_model=list[ProductOut])
def list_products(ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)) -> list[ProductOut]:
    products = db.scalars(select(Product).where(Product.store_id == ctx.store_id).order_by(Product.name)).all()
    return [ProductOut.model_validate(p) for p in products]


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: str, ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)), db: Session = Depends(get_db)) -> ProductOut:
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    return ProductOut.model_validate(product)


@router.put("/{product_id}/cost-price", response_model=ProductOut)
def set_product_cost_price(
    product_id: str,
    payload: ProductCostPriceIn,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProductOut:
    """Себестоимость — Ozon never exposes this via any API (it's the
    seller's own private purchase/production cost), so it's entered here
    once per product rather than via a repeated file upload. Used to
    compute margin/ROI on the dashboard, which is otherwise impossible from
    any Ozon data source."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    product.cost_price_rub = payload.cost_price_rub
    db.commit()
    record_audit(
        db, action="product_cost_price_set", user_id=user.id, store_id=ctx.store_id,
        target_type="product", target_id=product.id,
        message=f"Себестоимость установлена: {payload.cost_price_rub}",
    )
    db.refresh(product)
    return ProductOut.model_validate(product)


@router.post("/upload-localization", response_model=ImportSummary)
async def upload_product_localization(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Imports Ozon's own «Планирование поставок → Локальность продаж»
    export (XLSX, «По товарам»): the only confirmed source of per-product
    localization share (Ozon's Seller API only exposes this per whole
    store — see app.services.product_localization_import's own docstring
    for the full story and why a manual re-upload is the only option)."""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживается только файл .xlsx")

    content = await file.read()

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.XLSX_IMPORT,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    result = import_product_localization_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    db.commit()

    record_audit(
        db, action="product_localization_imported", user_id=user.id, store_id=ctx.store_id,
        target_type="sync_run", target_id=run.id,
        message=f"Импортирована локализация: {result.created} товаров",
    )

    return ImportSummary(fetched=result.fetched, created=result.created, skipped_duplicate=result.skipped_duplicate, errors=result.errors)


@router.post("/upload-cost-price", response_model=ImportSummary)
async def upload_product_cost_price(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Bulk-imports себестоимость from the seller's own XLSX spreadsheet,
    matched by "Артикул продавца" (offer_id) — see
    app.services.product_cost_price_import's own docstring for why offer_id
    rather than SKU, and which column is read."""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживается только файл .xlsx")

    content = await file.read()

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=SyncSourceType.XLSX_IMPORT,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    result = import_product_cost_price_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    db.commit()

    record_audit(
        db, action="product_cost_price_imported", user_id=user.id, store_id=ctx.store_id,
        target_type="sync_run", target_id=run.id,
        message=f"Импортирована себестоимость: {result.created} товаров",
    )

    return ImportSummary(fetched=result.fetched, created=result.created, skipped_duplicate=result.skipped_duplicate, errors=result.errors)


def _serialize_product_card_ai_review(r: ProductCardAiReview) -> ProductCardAiReviewOut:
    return ProductCardAiReviewOut(
        id=r.id,
        period_start=r.period_start,
        period_end=r.period_end,
        overview=r.overview,
        trend_observations=json.loads(r.trend_observations_json) if r.trend_observations_json else [],
        hypotheses=json.loads(r.hypotheses_json) if r.hypotheses_json else [],
        recommendations=json.loads(r.recommendations_json) if r.recommendations_json else [],
        reviews_considered=r.reviews_considered,
        model_used=r.model_used,
        created_at=r.created_at,
    )


@router.get("/{product_id}/ai-review", response_model=ProductCardAiReviewListResponse)
def list_product_card_ai_reviews(
    product_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
) -> ProductCardAiReviewListResponse:
    """History of full-card AI analyses (see
    app.services.product_card_ai_review_service) for one product, newest
    first — manual-trigger only, see POST .../ai-review/generate."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")
    rows = (
        db.query(ProductCardAiReview)
        .filter(ProductCardAiReview.store_id == ctx.store_id, ProductCardAiReview.product_id == product_id)
        .order_by(ProductCardAiReview.period_end.desc())
        .limit(30)
        .all()
    )
    return ProductCardAiReviewListResponse(items=[_serialize_product_card_ai_review(r) for r in rows])


@router.post("/{product_id}/ai-review/generate", response_model=ProductCardAiReviewOut)
def generate_product_card_ai_review_now(
    product_id: str,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductCardAiReviewOut:
    """Triggers one full-card AI analysis for this product immediately —
    combines already-collected advertising/order trends with the review
    findings already shown on this product's other tabs (see
    app.services.product_card_ai_review_service's own docstring for why
    this has no automatic daily scheduler, unlike the store-wide
    advertising review). Runs synchronously (a single AI call) so this
    returns the finished analysis directly."""
    product = db.get(Product, product_id)
    if not product or product.store_id != ctx.store_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Товар не найден")

    ai_provider = get_ai_provider()
    outcome = generate_product_card_ai_review(
        db, store_id=ctx.store_id, product_id=product_id, ai_provider=ai_provider, date_from=date_from, date_to=date_to,
    )
    if not outcome.saved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(outcome.errors) or "Не удалось выполнить анализ карточки товара",
        )
    review = (
        db.query(ProductCardAiReview)
        .filter(ProductCardAiReview.store_id == ctx.store_id, ProductCardAiReview.product_id == product_id)
        .order_by(ProductCardAiReview.period_end.desc())
        .first()
    )
    return _serialize_product_card_ai_review(review)
