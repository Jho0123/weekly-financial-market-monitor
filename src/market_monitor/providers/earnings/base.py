"""Earnings provider contract."""

from datetime import date
from typing import List

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python < 3.8
    Protocol = object  # type: ignore

from ...models.earnings_event import EarningsEvent


class EarningsProvider(Protocol):
    name: str

    def get_earnings(
        self, symbols: List[str], start: date, end: date
    ) -> List[EarningsEvent]:
        ...
