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
    """The window the weekly report covers.

    With the default ``days_ahead`` of 7 this is the Monday..Sunday week
    *following* the reference moment, so a Sunday 10:00 run reports on the
    week that starts the next day. A run on any other weekday reports on
    the next upcoming week the same way.

    Any other positive ``days_ahead`` switches to a rolling
    ``today .. today + days_ahead`` window, which is what a user who sets
    ``days_ahead: 14`` is asking for.
    """
    today = reference.date()

    if days_ahead == 7:
        # weekday(): Monday is 0. Always land on the *next* Monday, so a
        # Monday run reports the week ahead rather than the current one.
        days_until_monday = 7 - today.weekday()
        start = today + timedelta(days=days_until_monday)
        return start, start + timedelta(days=6)

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
