"""Repository contract.

The services depend on this shape, not on SQLite, so the storage backend
can be swapped without touching the pipeline.
"""

from datetime import date, datetime
from typing import List, Optional, Tuple

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python < 3.8
    Protocol = object  # type: ignore

from ..models.earnings_event import EarningsEvent
from ..models.macro_event import MacroEvent
from ..models.weekly_report import WeeklyReport


class Repository(Protocol):
    def save_macro_snapshot(self, source: str, events: List[MacroEvent]) -> None:
        ...

    def load_macro_snapshot(
        self, source: str, start: date, end: date
    ) -> Optional[Tuple[List[MacroEvent], datetime]]:
        ...

    def save_earnings_snapshot(self, events: List[EarningsEvent]) -> None:
        ...

    def load_earnings_snapshot(
        self, start: date, end: date
    ) -> Optional[Tuple[List[EarningsEvent], datetime]]:
        ...

    def save_report(self, report: WeeklyReport) -> None:
        ...

    def load_latest_report(self) -> Optional[WeeklyReport]:
        ...
