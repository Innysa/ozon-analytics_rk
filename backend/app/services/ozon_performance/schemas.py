"""Typed, tolerant wrappers around Ozon Performance API payloads.

Field names below (id, title, state, advObjectType, fromDate, toDate,
dailyBudget) were verified against current public documentation and
community references at the time this was written for the campaign-list
endpoint (GET /api/client/campaign). The exact numeric format/unit of
`dailyBudget` (rubles vs. kopecks, string vs. number) was not independently
confirmed, so it is parsed defensively (best-effort float conversion, else
left unset) rather than assumed. Verify against
https://docs.ozon.ru/api/performance/ before relying on a new field, and
before ever displaying `daily_budget_rub` as an authoritative figure.
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OzonTokenResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    access_token: str
    token_type: str | None = None
    expires_in: int | None = None


class OzonCampaignItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | int
    title: str | None = None
    state: str | None = None
    advObjectType: str | None = None
    fromDate: str | None = None
    toDate: str | None = None
    dailyBudget: str | int | float | None = None

    @property
    def daily_budget_rub(self) -> float | None:
        if self.dailyBudget is None:
            return None
        try:
            return float(self.dailyBudget)
        except (TypeError, ValueError):
            return None


class OzonCampaignListResponse(BaseModel):
    # `list` is the literal key Ozon uses in the JSON response; the Python
    # attribute is named `campaigns` (via alias) to avoid shadowing the
    # builtin `list` type inside this module's own annotations.
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    campaigns: List[OzonCampaignItem] = Field(default_factory=list, alias="list")

    @field_validator("campaigns", mode="before")
    @classmethod
    def _default_empty(cls, v):
        return v or []
