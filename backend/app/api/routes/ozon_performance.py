from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.core.encryption import decrypt_secret, encrypt_secret, mask_secret
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.ozon_credentials import OzonCredentials
from app.schemas.advertising import PerformanceCredentialsIn, PerformanceCredentialsOut
from app.services.audit import record_audit
from app.services.ozon_performance.client import OzonPerformanceClient
from app.services.ozon_performance.client import PerformanceCredentials as OzonPerfCredentials

router = APIRouter(prefix="/api/stores/{store_id}/ozon/performance", tags=["ozon-performance"])


def _get_or_none(db: Session, store_id: str) -> OzonCredentials | None:
    return db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()


def _to_out(creds: OzonCredentials | None, client_id: str | None = None, client_secret: str | None = None) -> PerformanceCredentialsOut:
    if not creds or not creds.performance_client_id_encrypted:
        return PerformanceCredentialsOut(configured=False)
    return PerformanceCredentialsOut(
        configured=True,
        client_id_masked=mask_secret(client_id or decrypt_secret(creds.performance_client_id_encrypted)),
        client_secret_masked=mask_secret(client_secret or decrypt_secret(creds.performance_client_secret_encrypted)),
        last_connection_check_at=creds.performance_last_connection_check_at,
        last_connection_ok=creds.performance_last_connection_ok,
        last_connection_message=creds.performance_last_connection_message,
    )


@router.get("/credentials", response_model=PerformanceCredentialsOut)
def get_performance_credentials(
    ctx: StoreContext = Depends(require_store_role(StoreRole.OWNER)), db: Session = Depends(get_db)
) -> PerformanceCredentialsOut:
    return _to_out(_get_or_none(db, ctx.store_id))


@router.put("/credentials", response_model=PerformanceCredentialsOut)
def set_performance_credentials(
    payload: PerformanceCredentialsIn,
    ctx: StoreContext = Depends(require_store_role(StoreRole.OWNER)),
    db: Session = Depends(get_db),
) -> PerformanceCredentialsOut:
    creds = _get_or_none(db, ctx.store_id)
    if not creds:
        # Seller API keys are configured independently (may be set later, or never).
        creds = OzonCredentials(store_id=ctx.store_id)
        db.add(creds)

    creds.performance_client_id_encrypted = encrypt_secret(payload.client_id)
    creds.performance_client_secret_encrypted = encrypt_secret(payload.client_secret)
    creds.performance_last_connection_ok = None
    creds.performance_last_connection_message = None
    db.flush()
    record_audit(db, action="ozon_performance_credentials_updated", store_id=ctx.store_id, target_type="ozon_credentials", target_id=creds.id)
    db.commit()
    return _to_out(creds, client_id=payload.client_id, client_secret=payload.client_secret)


@router.post("/check-connection", response_model=PerformanceCredentialsOut)
def check_performance_connection(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)), db: Session = Depends(get_db)
) -> PerformanceCredentialsOut:
    creds = _get_or_none(db, ctx.store_id)
    if not creds or not creds.performance_client_id_encrypted:
        return PerformanceCredentialsOut(configured=False, last_connection_ok=False, last_connection_message="Ключи Ozon Performance API не заданы")

    client_id = decrypt_secret(creds.performance_client_id_encrypted)
    client_secret = decrypt_secret(creds.performance_client_secret_encrypted)
    with OzonPerformanceClient(OzonPerfCredentials(client_id=client_id, client_secret=client_secret)) as client:
        outcome = client.check_connection()

    creds.performance_last_connection_check_at = datetime.now(timezone.utc)
    creds.performance_last_connection_ok = outcome["ok"]
    creds.performance_last_connection_message = outcome["message"]
    db.flush()
    record_audit(
        db,
        action="ozon_performance_connection_check",
        store_id=ctx.store_id,
        target_type="ozon_credentials",
        target_id=creds.id,
        result="success" if outcome["ok"] else "failure",
        message=outcome["message"],
    )
    db.commit()
    return _to_out(creds, client_id=client_id, client_secret=client_secret)
