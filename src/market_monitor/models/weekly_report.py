"""The single object the dashboard consumes (spec sections 4 and 14)."""

from datetime import date, datetime
from typing import List

from pydantic import BaseModel, Field

from .earnings_event import EarningsEvent
from .macro_event import MacroEvent
from .provider_status import ProviderStatus


class WeeklyReport(BaseModel):
    generated_at: datetime

    macro_start: date
    macro_end: date

    earnings_start: date
    earnings_end: date

    macro_events: List[MacroEvent] = Field(default_factory=list)
    earnings_events: List[EarningsEvent] = Field(default_factory=list)

    warnings: List[str] = Field(default_factory=list)
    provider_statuses: List[ProviderStatus] = Field(default_factory=list)

    @property
    def all_providers_ok(self) -> bool:
        return all(status.is_healthy for status in self.provider_statuses)
