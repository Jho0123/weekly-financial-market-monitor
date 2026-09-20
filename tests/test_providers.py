"""Provider parsing, driven by saved fixtures (spec section 50).

None of these tests touch the network. The fixtures are real responses
captured from each agency, so a change in their published format shows up
here as a failing parse rather than as an empty dashboard on a Sunday.
"""

from datetime import date, datetime, timezone


from market_monitor.providers.macro.bea import BEAProvider
from market_monitor.providers.macro.bls import BLSProvider
from market_monitor.providers.macro.census import CensusProvider
from market_monitor.providers.macro.federal_reserve import FederalReserveProvider
from market_monitor.utils.ics import parse_ics, resolve_tzid

from .conftest import fixture_text


class OfflineFetcher:
    """Serves fixture text instead of making a request."""

    def __init__(self, text):
        self.text = text
        self.urls = []

    def get_text(self, url):
        self.urls.append(url)
        return self.text


def provider_with(cls, fixture_name):
    return cls(fetcher=OfflineFetcher(fixture_text(fixture_name)))


def names(events):
    return {event.raw_name for event in events}


# ------------------------------------------------------------------- ICS

def test_ics_unfolds_continuation_lines():
    # RFC 5545 folds at a fixed octet count, so a fold lands mid-word and
    # unfolding must drop exactly the CRLF and the one leading space --
    # this is how the real BEA feed wraps its longer titles.
    text = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Gross Domestic Product\\, 4th Quarter and Year 2026 (Advance Estima\r\n"
        " te)\r\n"
        "DTSTART;VALUE=DATE-TIME:20260129T133000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = parse_ics(text)
    assert len(events) == 1
    assert events[0].summary == (
        "Gross Domestic Product, 4th Quarter and Year 2026 (Advance Estimate)"
    )
    assert events[0].start == datetime(2026, 1, 29, 13, 30, tzinfo=timezone.utc)


def test_ics_escaped_commas_are_restored():
    text = (
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Personal Income and Outlays\\, August 2026\r\n"
        "DTSTART;VALUE=DATE-TIME:20260925T123000Z\r\n"
        "END:VEVENT\r\n"
    )
    assert parse_ics(text)[0].summary == "Personal Income and Outlays, August 2026"


def test_ics_ignores_calendar_level_properties():
    text = (
        "BEGIN:VCALENDAR\r\n"
        "SUMMARY:BLS.gov Economic News Release Schedule\r\n"
        "X-WR-CALNAME:BLS.gov Economic News Release Schedule\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Consumer Price Index\r\n"
        "DTSTART;TZID=US-Eastern:20261013T083000\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = parse_ics(text)
    assert [event.summary for event in events] == ["Consumer Price Index"]


def test_non_iana_tzid_is_mapped():
    # BLS publishes "US-Eastern", which zoneinfo cannot resolve directly.
    assert str(resolve_tzid("US-Eastern")) == "America/New_York"
    assert str(resolve_tzid("America/Chicago")) == "America/Chicago"
    assert str(resolve_tzid("Nowhere/Invented")) == "America/New_York"


def test_ics_all_day_entry_becomes_a_date():
    text = (
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Something\r\n"
        "DTSTART;VALUE=DATE:20260115\r\n"
        "END:VEVENT\r\n"
    )
    event = parse_ics(text)[0]
    assert event.all_day is True
    assert event.start == date(2026, 1, 15)


# ------------------------------------------------------------------- BLS

def test_bls_parses_the_release_calendar():
    events = provider_with(BLSProvider, "bls_schedule.ics").parse(
        fixture_text("bls_schedule.ics")
    )
    assert len(events) > 200
    assert "Consumer Price Index" in names(events)
    assert "Employment Situation" in names(events)
    assert "Producer Price Index" in names(events)
    assert all(event.source == "BLS" for event in events)


def test_bls_times_are_eastern_and_aware():
    events = provider_with(BLSProvider, "bls_schedule.ics").parse(
        fixture_text("bls_schedule.ics")
    )
    cpi = next(e for e in events if e.raw_name == "Consumer Price Index")

    assert cpi.datetime_local.tzinfo is not None
    assert cpi.datetime_utc.tzinfo is timezone.utc
    assert cpi.datetime_local.strftime("%H:%M") == "08:30"
    assert cpi.datetime_local.tzname() in ("EST", "EDT")


def test_bls_window_filter_is_inclusive():
    provider = provider_with(BLSProvider, "bls_schedule.ics")
    events = provider.get_events(date(2026, 9, 21), date(2026, 9, 27))
    assert events
    for event in events:
        assert date(2026, 9, 21) <= event.datetime_local.date() <= date(2026, 9, 27)


# ------------------------------------------------------------------- BEA

def test_bea_parses_the_release_calendar():
    events = provider_with(BEAProvider, "bea_schedule.ics").parse(
        fixture_text("bea_schedule.ics")
    )
    assert len(events) > 80
    assert any(name.startswith("Gross Domestic Product") for name in names(events))
    assert any(name.startswith("Personal Income and Outlays") for name in names(events))
    assert all(event.source == "BEA" for event in events)


def test_bea_utc_timestamps_are_converted_to_eastern():
    events = provider_with(BEAProvider, "bea_schedule.ics").parse(
        fixture_text("bea_schedule.ics")
    )
    gdp = next(e for e in events if e.raw_name.startswith("Gross Domestic Product,"))

    # BEA stamps 13:30Z, which is 08:30 Eastern during daylight time.
    assert gdp.datetime_local.tzname() in ("EST", "EDT")
    assert gdp.datetime_local.strftime("%H:%M") in ("08:30", "07:30")


# --------------------------------------------------------- Federal Reserve

def test_fed_parses_meetings_and_derives_the_decision_day():
    events = provider_with(FederalReserveProvider, "fed_calendar.html").parse(
        fixture_text("fed_calendar.html")
    )
    assert events

    decisions = {
        event.datetime_local.date()
        for event in events
        if event.raw_name == "Federal Reserve Interest Rate Decision"
    }

    # The January 2026 meeting runs 27-28; the decision lands on the 28th.
    assert date(2026, 1, 28) in decisions
    assert date(2026, 1, 27) not in decisions
    # September 2026 meeting: 15-16.
    assert date(2026, 9, 16) in decisions


def test_fed_decisions_are_at_two_pm_eastern():
    events = provider_with(FederalReserveProvider, "fed_calendar.html").parse(
        fixture_text("fed_calendar.html")
    )
    decision = next(
        e
        for e in events
        if e.raw_name == "Federal Reserve Interest Rate Decision"
        and e.datetime_local.date() == date(2026, 1, 28)
    )
    assert decision.datetime_local.strftime("%H:%M") == "14:00"
    assert decision.datetime_local.tzname() in ("EST", "EDT")


def test_fed_emits_a_statement_alongside_each_decision():
    events = provider_with(FederalReserveProvider, "fed_calendar.html").parse(
        fixture_text("fed_calendar.html")
    )
    decisions = [
        e for e in events if e.raw_name == "Federal Reserve Interest Rate Decision"
    ]
    statements = [e for e in events if e.raw_name == "FOMC Statement"]
    assert len(decisions) == len(statements)


def test_fed_cross_month_meeting_ends_in_the_second_month():
    """A meeting listed as "Apr/May" 30-1 decides on May 1."""
    html = """
    <div class="panel">
      <div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
      <div class="row fomc-meeting">
        <div class="fomc-meeting__month"><strong>Apr/May</strong></div>
        <div class="fomc-meeting__date">30-1</div>
      </div>
    </div>
    """
    events = FederalReserveProvider().parse(html)
    assert {e.datetime_local.date() for e in events} == {date(2026, 5, 1)}


def test_fed_single_day_meeting_is_handled():
    html = """
    <div class="panel">
      <div class="panel-heading"><h4><a id="1">2026 FOMC Meetings</a></h4></div>
      <div class="row fomc-meeting">
        <div class="fomc-meeting__month"><strong>October</strong></div>
        <div class="fomc-meeting__date">22 (notation vote)</div>
      </div>
    </div>
    """
    events = FederalReserveProvider().parse(html)
    assert {e.datetime_local.date() for e in events} == {date(2026, 10, 22)}


def test_fed_reads_the_minutes_release_date():
    events = provider_with(FederalReserveProvider, "fed_calendar.html").parse(
        fixture_text("fed_calendar.html")
    )
    minutes = {
        event.datetime_local.date()
        for event in events
        if event.raw_name == "FOMC Minutes"
    }
    # The January 2026 minutes were released February 18, 2026.
    assert date(2026, 2, 18) in minutes


def test_fed_ignores_the_search_panel():
    html = """
    <div class="panel">
      <div class="panel-heading"><h4>FOMC Search</h4></div>
      <div class="row fomc-meeting">
        <div class="fomc-meeting__month"><strong>January</strong></div>
        <div class="fomc-meeting__date">27-28</div>
      </div>
    </div>
    """
    assert FederalReserveProvider().parse(html) == []


# ---------------------------------------------------------------- Census

def test_census_parses_the_indicator_calendar():
    events = provider_with(CensusProvider, "census_calendar.html").parse(
        fixture_text("census_calendar.html")
    )
    assert len(events) > 100
    assert any(
        name.startswith("Advance Monthly Sales for Retail") for name in names(events)
    )
    assert all(event.source == "Census" for event in events)


def test_census_reads_the_time_from_the_sort_key():
    events = provider_with(CensusProvider, "census_calendar.html").parse(
        fixture_text("census_calendar.html")
    )
    durable = next(
        e for e in events if e.raw_name.startswith("Advance Report on Durable Goods")
    )
    assert durable.datetime_local.strftime("%H:%M") == "08:30"
    assert durable.datetime_local.tzinfo is not None


def test_census_skips_rows_without_a_usable_key():
    html = """
    <table>
      <tr><td>Some Release</td><td>January 1, 2026</td><td>10:00 AM</td></tr>
      <tr><td>Good Release</td>
          <td sorttable_customkey="202601141000">January 14, 2026</td>
          <td>10:00 AM</td></tr>
    </table>
    """
    events = CensusProvider().parse(html)
    assert [event.raw_name for event in events] == ["Good Release"]
