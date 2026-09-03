from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import StoreContext, require_store_role
from app.core.encryption import decrypt_secret, encrypt_secret, mask_secret
from app.db.session import get_db
from app.models.membership import StoreRole
from app.models.ozon_credentials import OzonCredentials
from app.schemas.store import OzonCredentialsIn, OzonCredentialsOut
from app.services.audit import record_audit
from app.services.ozon.client import OzonCredentials as OzonClientCredentials
from app.services.ozon.client import OzonSellerClient

router = APIRouter(prefix="/api/stores/{store_id}/ozon", tags=["ozon"])


def _get_or_none(db: Session, store_id: str) -> OzonCredentials | None:
    return db.query(OzonCredentials).filter(OzonCredentials.store_id == store_id).first()


@router.get("/credentials", response_model=OzonCredentialsOut)
def get_credentials(ctx: StoreContext = Depends(require_store_role(StoreRole.OWNER)), db: Session = Depends(get_db)) -> OzonCredentialsOut:
    creds = _get_or_none(db, ctx.store_id)
    if not creds:
        return OzonCredentialsOut(configured=False)
    return OzonCredentialsOut(
        configured=True,
        client_id_masked=mask_secret(decrypt_secret(creds.client_id_encrypted)),
        api_key_masked=mask_secret(decrypt_secret(creds.api_key_encrypted)),
        last_connection_check_at=creds.last_connection_check_at,
        last_connection_ok=creds.last_connection_ok,
        last_connection_message=creds.last_connection_message,
        reviews_api_available=creds.reviews_api_available,
    )


@router.put("/credentials", response_model=OzonCredentialsOut)
def set_credentials(
    payload: OzonCredentialsIn,
    ctx: StoreContext = Depends(require_store_role(StoreRole.OWNER)),
    db: Session = Depends(get_db),
) -> OzonCredentialsOut:
    creds = _get_or_none(db, ctx.store_id)
    if not creds:
        creds = OzonCredentials(
            store_id=ctx.store_id,
            client_id_encrypted=encrypt_secret(payload.client_id),
            api_key_encrypted=encrypt_secret(payload.api_key),
        )
        db.add(creds)
    else:
        creds.client_id_encrypted = encrypt_secret(payload.client_id)
        creds.api_key_encrypted = encrypt_secret(payload.api_key)
        creds.last_connection_ok = None
        creds.last_connection_message = None
    db.flush()
    # Never log the actual key values.
    record_audit(db, action="ozon_credentials_updated", store_id=ctx.store_id, target_type="ozon_credentials", target_id=creds.id)
    db.commit()
    return OzonCredentialsOut(
        configured=True,
        client_id_masked=mask_secret(payload.client_id),
        api_key_masked=mask_secret(payload.api_key),
        last_connection_check_at=creds.last_connection_check_at,
        last_connection_ok=creds.last_connection_ok,
        last_connection_message=creds.last_connection_message,
        reviews_api_available=creds.reviews_api_available,
    )


@router.post("/check-connection", response_model=OzonCredentialsOut)
def check_connection(
    ctx: StoreContext = Depends(require_store_role(StoreRole.MANAGER)), db: Session = Depends(get_db)
) -> OzonCredentialsOut:
    creds = _get_or_none(db, ctx.store_id)
    if not creds:
        return OzonCredentialsOut(configured=False, last_connection_ok=False, last_connection_message="Ключи Ozon не заданы")

    client_id = decrypt_secret(creds.client_id_encrypted)
    api_key = decrypt_secret(creds.api_key_encrypted)
    with OzonSellerClient(OzonClientCredentials(client_id=client_id, api_key=api_key)) as client:
        outcome = client.check_connection()

    creds.last_connection_check_at = datetime.now(timezone.utc)
    creds.last_connection_ok = outcome["ok"]
    creds.last_connection_message = outcome["message"]
    creds.reviews_api_available = outcome["reviews_api_available"]
    db.flush()
    record_audit(
        db,
        action="ozon_connection_check",
        store_id=ctx.store_id,
        target_type="ozon_credentials",
        target_id=creds.id,
        result="success" if outcome["ok"] else "failure",
        message=outcome["message"],
    )
    db.commit()

    return OzonCredentialsOut(
        configured=True,
        client_id_masked=mask_secret(client_id),
        api_key_masked=mask_secret(api_key),
        last_connection_check_at=creds.last_connection_check_at,
        last_connection_ok=creds.last_connection_ok,
        last_connection_message=creds.last_connection_message,
        reviews_api_available=creds.reviews_api_available,
    )
