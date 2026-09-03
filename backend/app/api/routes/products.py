from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.schemas.product import ProductOut

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
