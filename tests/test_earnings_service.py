"""Earnings filtering and the Alpha Vantage adapter (spec section 54)."""

from datetime import date, datetime

import pytest

from market_monitor.models.earnings_event import (
    SESSION_AFTER,
    SESSION_BEFORE,
    SESSION_UNKNOWN,
    STATUS_CONFIRMED,
    STATUS_ESTIMATED,
    STATUS_EXPECTED,
    EarningsEvent,
)
from market_monitor.providers.earnings.alphavantage import (
    AlphaVantageEarningsProvider,
)
from market_monitor.services.earnings_service import EarningsService

from .conftest import TORONTO, StubEarningsProvider, fixture_text

REFERENCE = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)


def earnings(symbol: str, day: date, **kwargs) -> EarningsEvent:
    kwargs.setdefault("source", "Stub")
    return EarningsEvent(symbol=symbol, report_date=day, **kwargs)


def run(config, events):
    service = EarningsService(StubEarningsProvider(events=events), config)
    return service.fetch_upcoming(REFERENCE)


def test_window_is_today_plus_lookahead(config):
    service = EarningsService(StubEarningsProvider(), config)
    assert service.window(REFERENCE) == (date(2026, 9, 20), date(2026, 10, 20))


def test_configured_ticker_included_unconfigured_excluded(config):
    events, _, _ = run(
        config,
        [
            earnings("NVDA", date(2026, 10, 1)),
            earnings("TSLA", date(2026, 10, 2)),
        ],
    )
    assert [e.symbol for e in events] == ["NVDA"]


def test_dates_inside_and_outside_the_window(config):
    events, _, _ = run(
        config,
        [
            earnings("NVDA", date(2026, 9, 19)),  # yesterday
            earnings("NVDA", date(2026, 9, 20)),  # today, inclusive
            earnings("MU", date(2026, 10, 20)),  # last day, inclusive
            earnings("MU", date(2026, 10, 21)),  # one day past
        ],
    )
    assert [e.report_date for e in events] == [date(2026, 9, 20), date(2026, 10, 20)]


def test_lowercase_ticker_from_provider_is_normalized(config):
    events, _, _ = run(config, [earnings("nvda", date(2026, 10, 1))])
    assert [e.symbol for e in events] == ["NVDA"]


def test_duplicate_entries_are_deduplicated(config):
    events, _, _ = run(
        config,
        [
            earnings("NVDA", date(2026, 10, 1)),
            earnings("NVDA", date(2026, 10, 1)),
        ],
    )
    assert len(events) == 1


def test_events_sorted_by_date_then_symbol(config):
    events, _, _ = run(
        config,
        [
            earnings("NVDA", date(2026, 10, 5)),
            earnings("MU", date(2026, 10, 5)),
            earnings("NVDA", date(2026, 10, 1)),
        ],
    )
    assert [(e.report_date, e.symbol) for e in events] == [
        (date(2026, 10, 1), "NVDA"),
        (date(2026, 10, 5), "MU"),
        (date(2026, 10, 5), "NVDA"),
    ]


def test_days_away_is_computed_from_the_window_start(config):
    events, _, _ = run(config, [earnings("NVDA", date(2026, 9, 30))])
    assert events[0].days_away(date(2026, 9, 20)) == 10


def test_extending_the_lookahead_admits_later_dates(config):
    config.earnings.lookahead_days = 45
    events, _, _ = run(config, [earnings("NVDA", date(2026, 11, 3))])
    assert len(events) == 1


def test_disabled_earnings_are_skipped_without_calling_the_provider(config):
    config.earnings.enabled = False
    provider = StubEarningsProvider(events=[earnings("NVDA", date(2026, 10, 1))])
    events, status, _ = EarningsService(provider, config).fetch_upcoming(REFERENCE)

    assert events == []
    assert status.status == "skipped"
    assert provider.calls == 0


# --------------------------------------------------------- Alpha Vantage

def parse_fixture(symbols):
    return AlphaVantageEarningsProvider(api_key="test").parse(
        fixture_text("earnings_calendar.csv"), symbols
    )


def test_alphavantage_parses_the_watchlist_from_the_fixture():
    events = parse_fixture(["NVDA", "MU", "AMD", "MSFT"])
    by_symbol = {event.symbol: event for event in events}

    assert set(by_symbol) == {"MU", "AMD", "MSFT"}
    assert by_symbol["MU"].report_date == date(2026, 9, 30)
    assert by_symbol["MU"].session == SESSION_AFTER
    assert by_symbol["MU"].company_name == "MICRON TECHNOLOGY INCORPORATED"
    assert by_symbol["MU"].estimated_eps == pytest.approx(31.24)


def test_alphavantage_never_claims_a_date_is_confirmed():
    events = parse_fixture(["NVDA", "MU", "AMD", "MSFT"])
    assert events
    assert all(event.status != STATUS_CONFIRMED for event in events)


def test_alphavantage_status_reflects_whether_a_session_is_known():
    events = {e.symbol: e for e in parse_fixture(["MU", "AMD"])}
    assert events["MU"].status == STATUS_EXPECTED  # has post-market
    assert events["AMD"].status == STATUS_ESTIMATED  # no session given
    assert events["AMD"].session == SESSION_UNKNOWN


def test_alphavantage_ignores_symbols_off_the_watchlist():
    events = parse_fixture(["MU"])
    assert {event.symbol for event in events} == {"MU"}


def test_alphavantage_rejects_a_json_error_body():
    provider = AlphaVantageEarningsProvider(api_key="test")
    with pytest.raises(ValueError, match="message instead of CSV"):
        provider.parse('{"Information": "rate limit reached"}', ["MU"])


def test_alphavantage_rejects_an_unexpected_header():
    provider = AlphaVantageEarningsProvider(api_key="test")
    with pytest.raises(ValueError, match="Unexpected"):
        provider.parse("a,b,c\n1,2,3\n", ["MU"])


def test_alphavantage_skips_rows_with_unusable_dates():
    csv = (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
        "MU,Micron,,2026-08-31,1.0,USD,post-market\n"
        "MU,Micron,not-a-date,2026-08-31,1.0,USD,post-market\n"
        "MU,Micron,2026-09-30,2026-08-31,,USD,pre-market\n"
    )
    events = AlphaVantageEarningsProvider(api_key="test").parse(csv, ["MU"])
    assert len(events) == 1
    assert events[0].session == SESSION_BEFORE
    assert events[0].estimated_eps is None


@pytest.mark.parametrize(
    "days,expected",
    [(30, "3month"), (90, "3month"), (120, "6month"), (200, "12month")],
)
def test_horizon_scales_with_the_lookahead(days, expected):
    assert AlphaVantageEarningsProvider.horizon_for(days) == expected


def test_request_url_carries_the_calendar_function_and_key():
    url = AlphaVantageEarningsProvider(api_key="k123").build_url("6month")
    assert "function=EARNINGS_CALENDAR" in url
    assert "horizon=6month" in url
    assert "apikey=k123" in url
