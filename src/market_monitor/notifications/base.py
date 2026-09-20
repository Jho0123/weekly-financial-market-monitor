"""Notifier contract.

A notification failure must never fail the refresh (spec section 44), so
``send_safely`` swallows and logs instead of raising.
"""

import logging

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python < 3.8
    Protocol = object  # type: ignore

from ..models.weekly_report import WeeklyReport

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    name: str

    def send(self, report: WeeklyReport) -> None:
        ...


def send_safely(notifier, report: WeeklyReport) -> bool:
    try:
        notifier.send(report)
    except Exception as exc:  # noqa: BLE001 - never break the refresh
        logger.error("Notification via %s failed: %s", notifier.name, exc)
        return False
    logger.info("Notification sent via %s", notifier.name)
    return True
