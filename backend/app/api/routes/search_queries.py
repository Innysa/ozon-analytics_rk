from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.product import Product
from app.models.search_query_statistic import SearchQueryStatistic
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.common import ImportSummary
from app.schemas.search_query import (
    SearchQueryAnalyticsOut,
    SearchQueryStatisticListResponse,
    SearchQueryStatisticOut,
)
from app.services.audit import record_audit
from app.services.search_query_analytics_service import compute_search_query_analytics
from app.services.search_query_import import import_search_query_statistics_from_file

router = APIRouter(prefix="/api/stores/{store_id}/search-queries", tags=["search-queries"])

_ALLOWED_EXT = (".csv", ".xlsx")


@router.post("/upload", response_model=ImportSummary)
async def upload_search_queries(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Imports Ozon's own "Аналитика → Запросы" export (CSV/XLSX, sheet
    "Запросы моего товара"): real per-query search/order data for one or
    more products over the report's period. See
    app.services.search_query_import."""
    if not file.filename or not file.filename.lower().endswith(_ALLOWED_EXT):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Поддерживаются только файлы .csv и .xlsx")

    content = await file.read()
    source_type = SyncSourceType.CSV_IMPORT if file.filename.lower().endswith(".csv") else SyncSourceType.XLSX_IMPORT

    run = SyncRun(
        store_id=ctx.store_id,
        initiated_by_user_id=user.id,
        source_type=source_type,
        status=SyncStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    result = import_search_query_statistics_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None

    record_audit(
        db,
        action="search_queries_uploaded",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status != SyncStatus.FAILED else "failure",
        message=f"файл {file.filename}: создано {result.created}, дублей {result.skipped_duplicate}",
    )
    db.commit()

    return ImportSummary(
        fetched=result.fetched, created=result.created, skipped_duplicate=result.skipped_duplicate, errors=result.errors
    )


@router.get("", response_model=SearchQueryStatisticListResponse)
def list_search_queries(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> SearchQueryStatisticListResponse:
    stmt = select(SearchQueryStatistic).where(SearchQueryStatistic.store_id == ctx.store_id)
    if product_id:
        stmt = stmt.where(SearchQueryStatistic.product_id == product_id)
    if period_start:
        stmt = stmt.where(SearchQueryStatistic.period_end >= period_start)
    if period_end:
        stmt = stmt.where(SearchQueryStatistic.period_start <= period_end)

    rows = db.scalars(stmt.order_by(SearchQueryStatistic.people_searched.desc())).all()

    product_ids = {r.product_id for r in rows if r.product_id}
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(product_ids)).all()} if product_ids else {}

    items = [
        SearchQueryStatisticOut(
            id=r.id,
            product_id=r.product_id,
            product_name=products[r.product_id].name if r.product_id in products else None,
            ozon_sku=r.ozon_sku,
            offer_id=r.offer_id,
            query_text=r.query_text,
            period_start=r.period_start,
            period_end=r.period_end,
            people_searched=r.people_searched,
            people_saw=r.people_saw,
            position_ozon=r.position_ozon,
            conv_search_to_card_pct_ozon=r.conv_search_to_card_pct_ozon,
            conv_search_to_order_pct_ozon=r.conv_search_to_order_pct_ozon,
            ordered_units_by_query=r.ordered_units_by_query,
            ordered_sum_by_query_rub=r.ordered_sum_by_query_rub,
        )
        for r in rows
    ]
    return SearchQueryStatisticListResponse(items=items, total=len(items))


@router.get("/summary", response_model=SearchQueryAnalyticsOut)
def search_queries_summary(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    product_id: str | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> SearchQueryAnalyticsOut:
    return compute_search_query_analytics(
        db, store_id=ctx.store_id, product_id=product_id, period_start=period_start, period_end=period_end
    )
