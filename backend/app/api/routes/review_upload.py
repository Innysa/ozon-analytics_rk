from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, get_current_user, require_store_role
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.sync_run import SyncRun, SyncSourceType, SyncStatus
from app.models.user import User
from app.schemas.review import ImportSummary
from app.services.audit import record_audit
from app.services.review_import import import_reviews_from_file

router = APIRouter(prefix="/api/stores/{store_id}/reviews", tags=["reviews"])

_ALLOWED_EXT = (".csv", ".xlsx")


@router.post("/upload", response_model=ImportSummary)
async def upload_reviews(
    file: UploadFile,
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportSummary:
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

    result = import_reviews_from_file(db, store_id=ctx.store_id, filename=file.filename, content=content)

    run.finished_at = datetime.now(timezone.utc)
    run.items_fetched = result.fetched
    run.items_created = result.created
    run.items_skipped_duplicate = result.skipped_duplicate
    run.status = SyncStatus.SUCCESS if not result.errors else (SyncStatus.PARTIAL if result.created else SyncStatus.FAILED)
    run.error_message = "; ".join(result.errors[:20]) if result.errors else None

    record_audit(
        db,
        action="reviews_uploaded",
        user_id=user.id,
        store_id=ctx.store_id,
        target_type="sync_run",
        target_id=run.id,
        result="success" if run.status != SyncStatus.FAILED else "failure",
        message=f"файл {file.filename}: создано {result.created}, дублей {result.skipped_duplicate}",
    )
    db.commit()

    return ImportSummary(
        fetched=result.fetched,
        created=result.created,
        skipped_duplicate=result.skipped_duplicate,
        errors=result.errors,
    )
