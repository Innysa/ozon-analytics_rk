from datetime import datetime

from pydantic import BaseModel

from app.models.membership import StoreRole


class StoreOut(BaseModel):
    id: str
    name: str
    legal_name: str | None = None
    my_role: str  # "admin" for platform admins, else the StoreRole value
    last_sync_at: datetime | None = None

    model_config = {"from_attributes": True}


class StoreCreate(BaseModel):
    name: str
    legal_name: str | None = None


class MembershipOut(BaseModel):
    id: str
    user_id: str
    store_id: str
    role: StoreRole

    model_config = {"from_attributes": True}


class MembershipCreate(BaseModel):
    user_id: str
    store_id: str
    role: StoreRole


class OzonCredentialsIn(BaseModel):
    client_id: str
    api_key: str


class OzonCredentialsOut(BaseModel):
    configured: bool
    client_id_masked: str | None = None
    api_key_masked: str | None = None
    last_connection_check_at: datetime | None = None
    last_connection_ok: bool | None = None
    last_connection_message: str | None = None
    reviews_api_available: bool | None = None
