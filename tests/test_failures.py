"""Retry, failure isolation and last-known-good behaviour (spec section 55)."""

from datetime import date, datetime, timedelta

import httpx
import pytest

from market_monitor.models.earnings_event import EarningsEvent
from market_monitor.providers.macro.composite import CompositeMacroProvider
from market_monitor.repository.sqlite_repository import SqliteRepository
from market_monitor.services.earnings_service import EarningsService
from market_monitor.services.macro_service import MacroService
from market_monitor.services.normalizer import EventNormalizer
from market_monitor.utils.http import HttpFetcher
from market_monitor.utils.retry import RetryError, retry_call

from .conftest import (
    EASTERN,
    TORONTO,
    StubEarningsProvider,
    StubMacroProvider,
    make_macro_event,
)

REFERENCE = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)
CPI_AT = datetime(2026, 9, 22, 8, 30, tzinfo=EASTERN)


# ----------------------------------------------------------------- retry

def test_retry_gives_up_after_the_configured_attempts():
    attempts = []
    waits = []

    def always_fails():
        attempts.append(1)
        raise httpx.ConnectError("boom")

    with pytest.raises(RetryError):
        retry_call(
            always_fails,
            attempts=3,
            backoff_seconds=2,
            exceptions=(httpx.HTTPError,),
            sleep=waits.append,
        )

    assert len(attempts) == 3
    # Exponential backoff: 2s then 4s, and no sleep after the last try.
    assert waits == [2, 4]


def test_retry_succeeds_on_a_later_attempt():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("transient")
        return "ok"

    assert retry_call(
        flaky, attempts=3, backoff_seconds=0, exceptions=(httpx.HTTPError,),
        sleep=lambda _: None,
    ) == "ok"
    assert len(calls) == 3


def test_fetcher_retries_then_raises(monkeypatch):
    calls = []

    class FailingClient:
        def get(self, url):
            calls.append(url)
            raise httpx.ReadTimeout("timed out")

    fetcher = HttpFetcher(retries=3, backoff_seconds=0, client=FailingClient())
    monkeypatch.setattr("time.sleep", lambda _: None)

    with pytest.raises(RetryError):
        fetcher.get_text("https://example.invalid/feed.ics")
    assert len(calls) == 3


# ----------------------------------------------------- failure isolation

def test_one_failing_provider_does_not_break_the_others(config):
    composite = CompositeMacroProvider(
        [
            StubMacroProvider("BLS", error=RuntimeError("timeout")),
            StubMacroProvider(
                "Census",
                [make_macro_event("Consumer Price Index", CPI_AT, "Census")],
            ),
        ]
    )
    service = MacroService(composite, EventNormalizer(config.macro.events), config)
    events, statuses, warnings = service.fetch_week(REFERENCE)

    # The healthy provider's data still reaches the report.
    assert [e.canonical_name for e in events] == ["CPI"]

    by_name = {status.provider: status for status in statuses}
    assert by_name["BLS"].status == "error"
    assert by_name["Census"].status == "ok"
    assert any("BLS refresh failed" in warning for warning in warnings)


def test_earnings_failure_leaves_the_macro_report_intact(config, tmp_path):
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))
    service = EarningsService(
        StubEarningsProvider(error=RuntimeError("rate limited")),
        config,
        cache_loader=repository.load_earnings_snapshot,
    )
    events, status, warnings = service.fetch_upcoming(REFERENCE)

    assert events == []
    assert status.status == "error"
    assert warnings and "Earnings refresh failed" in warnings[0]


# -------------------------------------------------- last known good data

def test_failed_provider_falls_back_to_cached_data(config, tmp_path):
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))

    # A good run caches BLS.
    good = CompositeMacroProvider(
        [StubMacroProvider("BLS", [make_macro_event("Consumer Price Index", CPI_AT)])],
        cache_loader=repository.load_macro_snapshot,
        cache_saver=repository.save_macro_snapshot,
    )
    service = MacroService(good, EventNormalizer(config.macro.events), config)
    first, _, _ = service.fetch_week(REFERENCE)
    assert len(first) == 1

    # The next run fails outright.
    bad = CompositeMacroProvider(
        [StubMacroProvider("BLS", error=RuntimeError("503 Service Unavailable"))],
        cache_loader=repository.load_macro_snapshot,
        cache_saver=repository.save_macro_snapshot,
        local_tz=TORONTO,
    )
    service = MacroService(bad, EventNormalizer(config.macro.events), config)
    second, statuses, warnings = service.fetch_week(REFERENCE)

    # Old valid data survives, and the report says so.
    assert [e.canonical_name for e in second] == ["CPI"]
    assert statuses[0].status == "warning"
    assert statuses[0].from_cache is True
    assert warnings
    assert "Showing previously cached" in warnings[0]


def test_a_failed_fetch_never_overwrites_the_cache(config, tmp_path):
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))
    saves = []

    def recording_saver(name, events):
        saves.append((name, len(events)))
        repository.save_macro_snapshot(name, events)

    composite = CompositeMacroProvider(
        [StubMacroProvider("BLS", error=RuntimeError("down"))],
        cache_loader=repository.load_macro_snapshot,
        cache_saver=recording_saver,
    )
    composite.get_events(date(2026, 9, 21), date(2026, 9, 27))

    assert saves == []  # nothing written on the failure path


def test_cache_spans_beyond_the_reported_week(config, tmp_path):
    """Next week's fallback needs rows next week's window can see."""
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))

    this_week = make_macro_event("Consumer Price Index", CPI_AT)
    next_month = make_macro_event(
        "Consumer Price Index", CPI_AT + timedelta(days=30)
    )

    good = CompositeMacroProvider(
        [StubMacroProvider("BLS", [this_week, next_month])],
        cache_saver=repository.save_macro_snapshot,
    )
    in_window, _, _ = good.get_events(date(2026, 9, 21), date(2026, 9, 27))

    # Only this week is reported...
    assert len(in_window) == 1
    # ...but the snapshot kept the later event too.
    cached, _ = repository.load_macro_snapshot(
        "BLS", date(2026, 9, 21), date(2026, 12, 31)
    )
    assert len(cached) == 2


def test_earnings_fall_back_to_cache_and_refilter(config, tmp_path):
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))

    cached_rows = [
        EarningsEvent(symbol="NVDA", report_date=date(2026, 10, 1), source="Stub"),
        # Outside the 30-day window; must not reappear from cache.
        EarningsEvent(symbol="NVDA", report_date=date(2026, 12, 1), source="Stub"),
        # Not on the watchlist any more.
        EarningsEvent(symbol="TSLA", report_date=date(2026, 10, 2), source="Stub"),
    ]
    repository.save_earnings_snapshot(cached_rows)

    service = EarningsService(
        StubEarningsProvider(error=RuntimeError("429")),
        config,
        cache_loader=repository.load_earnings_snapshot,
        local_tz=TORONTO,
    )
    events, status, warnings = service.fetch_upcoming(REFERENCE)

    assert [(e.symbol, e.report_date) for e in events] == [
        ("NVDA", date(2026, 10, 1))
    ]
    assert status.status == "warning"
    assert status.from_cache is True
    assert "Showing previously cached" in warnings[0]


def test_cache_lookup_errors_do_not_break_the_report(config):
    def exploding_loader(name, start, end):
        raise RuntimeError("database locked")

    composite = CompositeMacroProvider(
        [StubMacroProvider("BLS", error=RuntimeError("down"))],
        cache_loader=exploding_loader,
    )
    events, statuses, warnings = composite.get_events(
        date(2026, 9, 21), date(2026, 9, 27)
    )

    assert events == []
    assert statuses[0].status == "error"
    assert warnings


def test_cache_save_errors_do_not_break_the_report(config):
    def exploding_saver(name, events):
        raise RuntimeError("disk full")

    composite = CompositeMacroProvider(
        [StubMacroProvider("BLS", [make_macro_event("Consumer Price Index", CPI_AT)])],
        cache_saver=exploding_saver,
    )
    events, statuses, _ = composite.get_events(date(2026, 9, 21), date(2026, 9, 27))

    assert len(events) == 1
    assert statuses[0].status == "ok"


def test_notification_failure_does_not_propagate():
    from market_monitor.notifications.base import send_safely
    from market_monitor.models.weekly_report import WeeklyReport

    class BrokenNotifier:
        name = "broken"

        def send(self, report):
            raise RuntimeError("webhook 500")

    report = WeeklyReport(
        generated_at=REFERENCE,
        macro_start=date(2026, 9, 21),
        macro_end=date(2026, 9, 27),
        earnings_start=date(2026, 9, 20),
        earnings_end=date(2026, 10, 20),
    )

    assert send_safely(BrokenNotifier(), report) is False
