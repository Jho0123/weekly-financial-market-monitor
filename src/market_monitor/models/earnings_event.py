"""Normalized earnings date for a watched symbol."""

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

# Spec sections 13: a future earnings date is rarely a promise. Keep the
# provider's confidence visible instead of flattening it.
STATUS_CONFIRMED = "confirmed"
STATUS_EXPECTED = "expected"
STATUS_ESTIMATED = "estimated"
STATUS_UNKNOWN = "unknown"
STATUSES = (STATUS_CONFIRMED, STATUS_EXPECTED, STATUS_ESTIMATED, STATUS_UNKNOWN)

SESSION_BEFORE = "before_market"
SESSION_AFTER = "after_market"
SESSION_DURING = "during_market"
SESSION_UNKNOWN = "unknown"
SESSIONS = (SESSION_BEFORE, SESSION_AFTER, SESSION_DURING, SESSION_UNKNOWN)

SESSION_LABELS = {
    SESSION_BEFORE: "Before Market",
    SESSION_AFTER: "After Market",
    SESSION_DURING: "During Market",
    SESSION_UNKNOWN: "Unknown",
}

STATUS_LABELS = {
    STATUS_CONFIRMED: "Confirmed",
    STATUS_EXPECTED: "Expected",
    STATUS_ESTIMATED: "Estimated",
    STATUS_UNKNOWN: "Unknown",
}


class EarningsEvent(BaseModel):
    symbol: str

    company_name: Optional[str] = None

    report_date: date

    session: str = SESSION_UNKNOWN

    estimated_eps: Optional[float] = None

    status: str = STATUS_UNKNOWN

    source: str
    source_url: Optional[str] = None

    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def dedupe_key(self):
        return (self.symbol, self.report_date)

    @property
    def session_label(self) -> str:
        return SESSION_LABELS.get(self.session, SESSION_LABELS[SESSION_UNKNOWN])

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, STATUS_LABELS[STATUS_UNKNOWN])

    def days_away(self, today: date) -> int:
        return (self.report_date - today).days
