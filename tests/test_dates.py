"""Window arithmetic and timezone handling (spec sections 20 and 52)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from market_monitor.utils.dates import (
    day_bounds,
    earnings_window,
    format_local,
    to_utc,
    week_window,
)

TORONTO = ZoneInfo("America/Toronto")


def test_sunday_run_covers_the_following_week():
    # The worked example from spec section 52.
    reference = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)
    assert week_window(reference) == (date(2026, 9, 21), date(2026, 9, 27))


def test_earnings_window_is_today_plus_lookahead():
    reference = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)
    assert earnings_window(reference, 30) == (date(2026, 9, 20), date(2026, 10, 20))


@pytest.mark.parametrize(
    "day,expected_start",
    [
        (14, date(2026, 9, 21)),  # Monday run -> the week ahead
        (16, date(2026, 9, 21)),  # Wednesday
        (19, date(2026, 9, 21)),  # Saturday
        (20, date(2026, 9, 21)),  # Sunday
    ],
)
def test_week_window_always_looks_forward(day, expected_start):
    reference = datetime(2026, 9, day, 10, 0, tzinfo=TORONTO)
    start, end = week_window(reference)
    assert start == expected_start
    assert start.weekday() == 0  # Monday
    assert end.weekday() == 6  # Sunday
    assert (end - start).days == 6


def test_days_ahead_zero_means_the_current_week():
    reference = datetime(2026, 9, 23, 10, 0, tzinfo=TORONTO)  # Wednesday
    assert week_window(reference, 0) == (date(2026, 9, 21), date(2026, 9, 27))


def test_custom_days_ahead_is_a_rolling_window():
    reference = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)
    assert week_window(reference, 14) == (date(2026, 9, 20), date(2026, 10, 4))


def test_day_bounds_are_inclusive_and_aware():
    start, end = day_bounds(date(2026, 9, 21), date(2026, 9, 27), TORONTO)
    assert start.hour == 0 and start.minute == 0
    assert end.hour == 23 and end.minute == 59
    assert start.tzinfo is not None and end.tzinfo is not None


def test_to_utc_refuses_naive_datetimes():
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 9, 20, 10, 0))


def test_dst_transition_keeps_the_wall_clock_time():
    # US DST ends Nov 1, 2026. A release at 08:30 local is 08:30 local on
    # both sides of the change, but a different UTC instant.
    before = datetime(2026, 10, 30, 8, 30, tzinfo=TORONTO)
    after = datetime(2026, 11, 2, 8, 30, tzinfo=TORONTO)

    assert before.utcoffset().total_seconds() == -4 * 3600
    assert after.utcoffset().total_seconds() == -5 * 3600
    assert to_utc(before).hour == 12
    assert to_utc(after).hour == 13


def test_format_local_strips_zero_padded_hours():
    moment = datetime(2026, 9, 22, 8, 30, tzinfo=TORONTO)
    assert format_local(moment, "%a %I:%M %p %Z") == "Tue 8:30 AM EDT"
