from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.user import User
from app.schemas.product import ProductCostPriceIn, ProductOut
from app.services.audit import record_audit

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
