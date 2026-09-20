"""EarningsAPI.com provider."""

import json
from datetime import date

import pytest

from market_monitor.models.earnings_event import (
    SESSION_AFTER,
    SESSION_BEFORE,
    SESSION_DURING,
    SESSION_UNKNOWN,
    STATUS_CONFIRMED,
    STATUS_ESTIMATED,
    STATUS_EXPECTED,
)
from market_monitor.providers.earnings.earningsapi import (
    EarningsApiProvider,
    InvalidApiKey,
    MissingApiKey,
)
from market_monitor.utils.http import PermanentHttpError, redact

from .conftest import fixture_text

WATCHLIST = ["NVDA", "MU"]


def nvda_rows():
    return json.loads(fixture_text("earningsapi_nvda.json"))


class JsonFetcher:
    """Serves a canned response per symbol and records the URLs used."""

    def __init__(self, by_symbol=None, default=None, error=None):
        self.by_symbol = by_symbol or {}
        self.default = default if default is not None else []
        self.error = error
        self.urls = []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        for symbol, payload in self.by_symbol.items():
            if "symbol={}".format(symbol) in url:
                return payload
        return self.default


def provider(fetcher=None, api_key="test-key"):
    return EarningsApiProvider(api_key=api_key, fetcher=fetcher or JsonFetcher())


# ------------------------------------------------------------------ parse

def test_parses_the_documented_array_shape():
    events = provider().parse(nvda_rows(), "NVDA")

    assert len(events) == 4
    first = next(e for e in events if e.report_date == date(2026, 11, 18))
    assert first.symbol == "NVDA"
    assert first.company_name == "NVIDIA Corporation"
    assert first.estimated_eps == pytest.approx(1.92)
    assert first.source == "EarningsAPI"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("time-pre-market", SESSION_BEFORE),
        ("time-after-hours", SESSION_AFTER),
        ("time-during-market", SESSION_DURING),
        ("BMO", SESSION_BEFORE),
        ("amc", SESSION_AFTER),
        ("time-not-supplied", SESSION_UNKNOWN),
        ("", SESSION_UNKNOWN),
        (None, SESSION_UNKNOWN),
        ("gibberish", SESSION_UNKNOWN),
    ],
)
def test_session_mapping(value, expected):
    assert EarningsApiProvider._parse_session(value) == expected


def test_status_reflects_whether_the_time_is_known():
    events = {e.report_date: e for e in provider().parse(nvda_rows(), "NVDA")}

    assert events[date(2026, 11, 18)].status == STATUS_EXPECTED  # after-hours
    assert events[date(2026, 10, 5)].status == STATUS_ESTIMATED  # not supplied


def test_never_claims_a_date_is_confirmed():
    """The API publishes no confirmation flag, so nothing may claim one."""
    events = provider().parse(nvda_rows(), "NVDA")
    assert events
    assert all(event.status != STATUS_CONFIRMED for event in events)


def test_envelope_shape_is_tolerated():
    events = provider().parse({"data": nvda_rows()}, "NVDA")
    assert len(events) == 4


def test_symbol_falls_back_to_the_requested_ticker():
    events = provider().parse([{"date": "2026-11-18"}], "MU")
    assert events[0].symbol == "MU"


def test_past_results_are_dropped_at_the_window_start():
    """The endpoint ships years of reported results; none belong here."""
    events = provider().parse(nvda_rows(), "NVDA", start=date(2026, 9, 20))

    assert [e.report_date for e in events] == [
        date(2026, 11, 18),
        date(2026, 10, 5),
    ]


def test_dates_past_the_window_end_are_kept_for_the_cache():
    """Keeping later dates gives next week's refresh a fallback."""
    fetcher = JsonFetcher(by_symbol={"NVDA": nvda_rows()})
    events = provider(fetcher).get_earnings(
        ["NVDA"], date(2026, 9, 20), date(2026, 10, 20)
    )
    # Nov 18 is outside the requested window but still returned; the
    # service is what trims it out of the report.
    assert date(2026, 11, 18) in {e.report_date for e in events}


def test_rows_with_unusable_dates_are_skipped():
    rows = [
        {"date": None},
        {"date": "not-a-date"},
        "not-an-object",
        {"date": "2026-11-18"},
    ]
    assert len(provider().parse(rows, "NVDA")) == 1


def test_unexpected_object_shape_is_an_error():
    with pytest.raises(ValueError, match="no earnings array"):
        provider().parse({"unexpected": True}, "NVDA")


def test_non_list_payload_is_an_error():
    with pytest.raises(ValueError, match="where a list was expected"):
        provider().parse("a string", "NVDA")


# ---------------------------------------------------------------- request

def test_one_request_per_symbol():
    fetcher = JsonFetcher(by_symbol={"NVDA": nvda_rows(), "MU": []})
    prov = provider(fetcher)
    prov.get_earnings(WATCHLIST, date(2026, 9, 20), date(2026, 12, 31))

    assert len(fetcher.urls) == 2
    assert any("symbol=NVDA" in url for url in fetcher.urls)
    assert any("symbol=MU" in url for url in fetcher.urls)


def test_symbols_are_normalized_and_deduplicated():
    fetcher = JsonFetcher()
    provider(fetcher).get_earnings(
        [" nvda ", "NVDA", "mu"], date(2026, 9, 20), date(2026, 12, 31)
    )
    assert len(fetcher.urls) == 2


def test_key_is_sent_as_a_query_parameter():
    url = provider().build_url("NVDA")
    assert url.startswith("https://api.earningsapi.com/v1/earnings?")
    assert "symbol=NVDA" in url
    assert "apikey=test-key" in url


def test_missing_key_explains_where_to_get_one(monkeypatch):
    # Other tests call load_dotenv(), which can put a real key into the
    # process environment; clear it so this exercises the empty case.
    monkeypatch.delenv("EARNINGS_API_KEY", raising=False)

    prov = EarningsApiProvider(api_key="", fetcher=JsonFetcher())
    assert prov.api_key == ""

    with pytest.raises(MissingApiKey, match="earningsapi.com"):
        prov.get_earnings(WATCHLIST, date(2026, 9, 20), date(2026, 10, 20))


def test_401_and_403_stop_immediately_rather_than_burning_quota():
    """A credential problem affects every symbol, so do not try the rest."""
    for status, expected in ((401, MissingApiKey), (403, InvalidApiKey)):
        fetcher = JsonFetcher(
            error=PermanentHttpError("HTTP", status_code=status, body="key")
        )
        with pytest.raises(expected):
            provider(fetcher).get_earnings(
                WATCHLIST, date(2026, 9, 20), date(2026, 10, 20)
            )
        assert len(fetcher.urls) == 1  # stopped after the first symbol


def test_unknown_ticker_is_not_treated_as_a_failure():
    class PerSymbol(JsonFetcher):
        def get_json(self, url, headers=None):
            self.urls.append(url)
            if "symbol=MU" in url:
                raise PermanentHttpError("HTTP 404", status_code=404, body="")
            return nvda_rows()

    events = provider(PerSymbol()).get_earnings(
        WATCHLIST, date(2026, 9, 20), date(2026, 12, 31)
    )
    assert {e.symbol for e in events} == {"NVDA"}


def test_a_real_per_symbol_failure_fails_the_whole_refresh():
    """Partial results would overwrite good cached rows for the rest."""

    class PerSymbol(JsonFetcher):
        def get_json(self, url, headers=None):
            self.urls.append(url)
            if "symbol=MU" in url:
                raise PermanentHttpError("HTTP 500", status_code=503, body="")
            return nvda_rows()

    with pytest.raises(RuntimeError, match="MU"):
        provider(PerSymbol()).get_earnings(
            WATCHLIST, date(2026, 9, 20), date(2026, 12, 31)
        )


def test_the_key_is_redacted_before_logging():
    url = provider().build_url("NVDA")
    assert "test-key" in url  # it must be sent
    assert "test-key" not in redact(url)  # but never logged
    assert "apikey=REDACTED" in redact(url)


# ------------------------------------------- integration with the service

def test_service_filters_history_out_of_the_window(config):
    """The endpoint returns past results too; only the window may show."""
    from market_monitor.services.earnings_service import EarningsService
    from datetime import datetime
    from .conftest import TORONTO

    config.earnings.symbols = ["NVDA"]
    config.earnings.lookahead_days = 30

    fetcher = JsonFetcher(by_symbol={"NVDA": nvda_rows()})
    service = EarningsService(provider(fetcher), config)

    events, status, warnings = service.fetch_upcoming(
        datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)
    )

    # Only the Oct 5 date falls inside Sep 20 .. Oct 20; the Nov date is
    # too far out and the Aug/May rows are already reported.
    assert [e.report_date for e in events] == [date(2026, 10, 5)]
    assert status.status == "ok"
    assert warnings == []
