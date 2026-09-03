from datetime import date, datetime

from pydantic import BaseModel


class PerformanceCredentialsIn(BaseModel):
    client_id: str
    client_secret: str


class PerformanceCredentialsOut(BaseModel):
    configured: bool
    client_id_masked: str | None = None
    client_secret_masked: str | None = None
    last_connection_check_at: datetime | None = None
    last_connection_ok: bool | None = None
    last_connection_message: str | None = None


class AdvertisingCampaignOut(BaseModel):
    id: str
    ozon_campaign_id: str
    name: str | None
    campaign_type: str | None
    state: str | None
    daily_budget_rub: float | None
    date_from: date | None
    date_to: date | None

    model_config = {"from_attributes": True}
