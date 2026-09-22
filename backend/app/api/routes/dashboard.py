from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.common import ImportSummary
from app.schemas.dashboard import DashboardOut
from app.services.accrual_report_import import import_accrual_report_from_file
from app.services.audit import record_audit
from app.services.dashboard_service import compute_dashboard

router = APIRouter(prefix="/api/stores/{store_id}/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(
    ctx: StoreContext = Depends(require_store_role(StoreRole.VIEWER)),
    db: Session = Depends(get_db),
    date_from: date | None = None,
    date_to: date | None = None,
) -> DashboardOut:
    return compute_dashboard(db, store_id=ctx.store_id, date_from=date_from, date_to=date_to)


@router.post("/upload-accruals", response_model=ImportSummary)
async def upload_accrual_report(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
    """Imports Ozon's own «Начисления» export (Финансы → Начисления →
    «Скачать отчёт», XLSX) — the only confirmed source of exact per-day,
    per-category logistics/services figures (Ozon's own «Группа услуг»/
    «Тип начисления» naming, including «Эквайринг» as its own row) — see
    app.services.accrual_report_import's own docstring for why this
    replaces the day-count-prorated cash-flow-statement estimate on the
    Дашборд's «Логистика и услуги» block whenever it covers the requested
    range, and why a manual re-upload is the only option (same situation
    as product localization)."""
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

    result = import_accrual_report_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    db.commit()

    record_audit(
        db, action="accrual_report_imported", user_id=user.id, store_id=ctx.store_id,
        target_type="sync_run", target_id=run.id,
        message=f"Импортирован отчёт «Начисления»: {result.created} записей (дата×группа×тип)",
    )

    return ImportSummary(
        fetched=result.fetched, created=result.created, skipped_duplicate=result.skipped_duplicate, errors=result.errors,
    )
