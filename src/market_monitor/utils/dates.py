"""Timezone-aware window arithmetic.

Naive datetimes are never produced or accepted here (spec section 20).
Boundaries are stated explicitly: both macro and earnings windows are
inclusive of their start and end dates.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Tuple
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def now_in(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def ensure_aware(moment: datetime, tz: ZoneInfo) -> datetime:
    """Attach ``tz`` to a naive datetime; leave aware ones untouched."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment


def to_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("refusing to convert a naive datetime to UTC")
    return moment.astimezone(timezone.utc)


def week_window(reference: datetime, days_ahead: int = 7) -> Tuple[date, date]:
    """The window the macro calendar covers.

    A rolling ``today .. today + days_ahead`` window, both ends inclusive,
    measured from the moment the run happens. With the default of 7 a run
    on Saturday Oct 10 covers Oct 10 through Oct 17.

    ``days_ahead`` of 0 means the current Monday..Sunday calendar week.
    """
    today = reference.date()

    if days_ahead == 0:
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)

    return today, today + timedelta(days=days_ahead)


def earnings_window(reference: datetime, lookahead_days: int) -> Tuple[date, date]:
    """``today .. today + lookahead_days``, both ends inclusive."""
    today = reference.date()
    return today, today + timedelta(days=lookahead_days)


def day_bounds(start: date, end: date, tz: ZoneInfo) -> Tuple[datetime, datetime]:
    """Turn an inclusive date range into an aware datetime range.

    Returns ``start 00:00:00`` through ``end 23:59:59.999999`` in ``tz``.
    """
    return (
        datetime.combine(start, time.min, tzinfo=tz),
        datetime.combine(end, time.max, tzinfo=tz),
    )


def format_local(moment: datetime, fmt: str = "%a %b %d, %I:%M %p %Z") -> str:
    text = moment.strftime(fmt)
    # Drop the zero padding %I insists on, without %-I (not portable).
    return text.replace(" 0", " ")
