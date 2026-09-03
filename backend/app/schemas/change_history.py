from datetime import datetime

from pydantic import BaseModel

from app.models.change_history import ChangeType


class ChangeHistoryOut(BaseModel):
    id: str
    store_id: str
    product_id: str
    user_id: str | None
    user_name: str | None = None
    change_type: ChangeType
    changed_at: datetime
    description: str
    comment: str | None

    model_config = {"from_attributes": True}


class ChangeHistoryCreate(BaseModel):
    product_id: str
    change_type: ChangeType
    changed_at: datetime
    description: str
    comment: str | None = None
