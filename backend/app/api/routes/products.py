from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.common import ImportSummary
from app.schemas.product import ProductCostPriceIn, ProductOut
from app.services.audit import record_audit
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
