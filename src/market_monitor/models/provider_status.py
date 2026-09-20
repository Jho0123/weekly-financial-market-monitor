"""Per-provider health, surfaced on the dashboard (spec sections 40 and 64)."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

STATUS_OK = "ok"
STATUS_WARNING = "warning"  # fetch failed, cached data is being shown
STATUS_ERROR = "error"  # fetch failed and nothing cached is available
STATUS_SKIPPED = "skipped"  # disabled in config

STATUS_LABELS = {
    STATUS_OK: "OK",
    STATUS_WARNING: "Warning",
    STATUS_ERROR: "Error",
    STATUS_SKIPPED: "Skipped",
}


class ProviderStatus(BaseModel):
    provider: str
    status: str = STATUS_OK
    last_success: Optional[datetime] = None
    last_attempt: Optional[datetime] = None
    error: Optional[str] = None
    event_count: int = 0
    from_cache: bool = False

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def is_healthy(self) -> bool:
        return self.status in (STATUS_OK, STATUS_SKIPPED)
