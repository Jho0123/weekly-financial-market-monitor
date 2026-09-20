"""End-to-end run against saved fixtures, plus the optional live checks.

The offline tests here wire the real providers to fixture text, so the
whole path -- parse, normalize, filter, dedupe, persist, report -- runs
without a network.
"""

from datetime import date, datetime

import pytest

from market_monitor.config import load_config
from market_monitor.providers.macro.bea import BEAProvider
from market_monitor.providers.macro.bls import BLSProvider
from market_monitor.providers.macro.census import CensusProvider
from market_monitor.providers.macro.composite import CompositeMacroProvider
from market_monitor.providers.macro.federal_reserve import FederalReserveProvider
from market_monitor.repository.sqlite_repository import SqliteRepository
from market_monitor.services.macro_service import MacroService
from market_monitor.services.normalizer import EventNormalizer
from market_monitor.services.report_service import build_weekly_report, summarize

from .conftest import TORONTO, fixture_text
from .test_config import REPO_ROOT
from .test_providers import OfflineFetcher

FIXTURE_PROVIDERS = [
    (BLSProvider, "bls_schedule.ics"),
    (BEAProvider, "bea_schedule.ics"),
    (FederalReserveProvider, "fed_calendar.html"),
    (CensusProvider, "census_calendar.html"),
]


@pytest.fixture
def shipped_config():
    return load_config(REPO_ROOT / "config" / "config.yaml")


def offline_composite(repository=None):
    providers = [
        cls(fetcher=OfflineFetcher(fixture_text(name)))
        for cls, name in FIXTURE_PROVIDERS
    ]
    return CompositeMacroProvider(
        providers,
        cache_loader=repository.load_macro_snapshot if repository else None,
        cache_saver=repository.save_macro_snapshot if repository else None,
        local_tz=TORONTO,
    )


def run_week(shipped_config, reference, repository=None):
    service = MacroService(
        offline_composite(repository),
        EventNormalizer(shipped_config.macro.events),
        shipped_config,
    )
    return service.fetch_week(reference)


def test_a_busy_week_produces_the_expected_calendar(shipped_config):
    """Week of Oct 12-18 2026: CPI, PPI and Retail Sales all land."""
    events, statuses, warnings = run_week(
        shipped_config, datetime(2026, 10, 11, 10, 0, tzinfo=TORONTO)
    )

    names = [event.canonical_name for event in events]
    assert "CPI" in names
    assert all(status.status == "ok" for status in statuses)
    assert warnings == []

    # Chronological, and every event carries the user's importance.
    assert events == sorted(events, key=lambda e: (e.datetime_local, e.canonical_name))
    assert all(event.importance >= shipped_config.macro.minimum_importance for event in events)


def test_an_fomc_week_includes_the_decision(shipped_config):
    """September 2026 FOMC runs 15-16; the decision is Wednesday the 16th."""
    events, _, _ = run_week(
        shipped_config, datetime(2026, 9, 13, 10, 0, tzinfo=TORONTO)
    )

    decisions = [e for e in events if e.canonical_name == "FOMC Rate Decision"]
    assert len(decisions) == 1
    assert decisions[0].datetime_local.date() == date(2026, 9, 16)
    assert decisions[0].datetime_local.strftime("%H:%M") == "14:00"
    assert decisions[0].importance == 5

    # The statement is a separate configured event at the same instant.
    statements = [e for e in events if e.canonical_name == "FOMC Statement"]
    assert len(statements) == 1


def test_every_reported_event_is_inside_the_window(shipped_config):
    reference = datetime(2026, 10, 11, 10, 0, tzinfo=TORONTO)
    events, _, _ = run_week(shipped_config, reference)

    for event in events:
        assert date(2026, 10, 12) <= event.datetime_local.date() <= date(2026, 10, 18)


def test_raising_the_minimum_importance_shrinks_the_report(shipped_config):
    reference = datetime(2026, 10, 11, 10, 0, tzinfo=TORONTO)

    wide, _, _ = run_week(shipped_config, reference)
    shipped_config.macro.minimum_importance = 5
    narrow, _, _ = run_week(shipped_config, reference)

    assert len(narrow) <= len(wide)
    assert all(event.importance == 5 for event in narrow)


def test_full_pipeline_persists_and_reloads(shipped_config, tmp_path):
    repository = SqliteRepository(str(tmp_path / "db.sqlite"))
    reference = datetime(2026, 10, 11, 10, 0, tzinfo=TORONTO)

    events, statuses, warnings = run_week(shipped_config, reference, repository)

    report = build_weekly_report(
        generated_at=reference,
        macro_start=date(2026, 10, 12),
        macro_end=date(2026, 10, 18),
        earnings_start=date(2026, 10, 11),
        earnings_end=date(2026, 11, 10),
        macro_events=events,
        earnings_events=[],
        warnings=warnings,
        provider_statuses=statuses,
    )
    repository.save_report(report)

    loaded = repository.load_latest_report()
    assert loaded is not None
    assert len(loaded.macro_events) == len(events)
    assert loaded.macro_events[0].datetime_local == events[0].datetime_local
    assert "Weekly Market Monitor" in summarize(loaded)


def test_no_duplicate_events_survive_the_pipeline(shipped_config):
    events, _, _ = run_week(
        shipped_config, datetime(2026, 10, 11, 10, 0, tzinfo=TORONTO)
    )
    keys = [event.dedupe_key for event in events]
    assert len(keys) == len(set(keys))


# ------------------------------------------------------------------ live

@pytest.mark.live
def test_live_sources_are_reachable_and_parse():
    """Run with `pytest -m live`. Hits the real agency endpoints."""
    from market_monitor.utils.http import HttpFetcher

    fetcher = HttpFetcher(timeout_seconds=30, retries=2, backoff_seconds=1)
    for cls, _ in FIXTURE_PROVIDERS:
        provider = cls(fetcher=fetcher)
        events = provider.parse(provider.fetch_text())
        assert events, "{} returned no events".format(provider.name)


@pytest.mark.live
def test_live_earningsapi_parses():
    """Needs EARNINGS_API_KEY. Run with `pytest -m live`."""
    import os
    from datetime import timedelta

    from market_monitor.providers.earnings.earningsapi import EarningsApiProvider

    if not os.environ.get("EARNINGS_API_KEY"):
        pytest.skip("EARNINGS_API_KEY is not set")

    # Two symbols only: the free plan allows 100 requests a day.
    symbols = ["NVDA", "MU"]
    events = EarningsApiProvider().get_earnings(
        symbols=symbols,
        start=date.today(),
        end=date.today() + timedelta(days=120),
    )
    assert isinstance(events, list)
    for event in events:
        assert event.symbol in set(symbols)
        assert event.source == "EarningsAPI"


@pytest.mark.live
def test_live_alphavantage_calendar_still_parses():
    from market_monitor.providers.earnings.alphavantage import (
        AlphaVantageEarningsProvider,
    )

    provider = AlphaVantageEarningsProvider()
    events = provider.get_earnings(
        symbols=["NVDA", "MU", "AMD", "MSFT"],
        start=date.today(),
        end=date.today(),
    )
    assert isinstance(events, list)
