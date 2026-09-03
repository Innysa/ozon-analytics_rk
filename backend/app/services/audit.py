from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    user_id: str | None = None,
    store_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    result: str = "success",
    message: str | None = None,
) -> None:
    """Append an audit entry. Callers must never pass secret values in `message`."""
    entry = AuditLog(
        action=action,
        user_id=user_id,
        store_id=store_id,
        target_type=target_type,
        target_id=target_id,
        result=result,
        message=message,
    )
    db.add(entry)
    db.flush()
