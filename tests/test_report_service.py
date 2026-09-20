"""Report assembly, JSON export and text summary (spec sections 43 and 57)."""

import json
from datetime import date, datetime

from market_monitor.models.earnings_event import EarningsEvent
from market_monitor.models.provider_status import ProviderStatus
from market_monitor.services.report_service import (
    build_weekly_report,
    importance_label,
    stars,
    summarize,
    write_json,
)

from .conftest import EASTERN, TORONTO, make_macro_event


def build(**overrides):
    defaults = dict(
        generated_at=datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO),
        macro_start=date(2026, 9, 21),
        macro_end=date(2026, 9, 27),
        earnings_start=date(2026, 9, 20),
        earnings_end=date(2026, 10, 20),
        macro_events=[],
        earnings_events=[],
        warnings=[],
        provider_statuses=[],
    )
    defaults.update(overrides)
    return build_weekly_report(**defaults)


def cpi():
    event = make_macro_event(
        "Consumer Price Index", datetime(2026, 9, 22, 8, 30, tzinfo=EASTERN)
    )
    return event.model_copy(update={"canonical_name": "CPI", "importance": 5})


def test_stars_and_labels():
    assert stars(5) == "★" * 5
    assert stars(3) == "★" * 3
    assert stars(0) == ""
    assert importance_label(5) == "Critical"
    assert importance_label(4) == "Very Important"
    assert importance_label(3) == "Important"


def test_summary_lists_critical_events_and_earnings():
    report = build(
        macro_events=[cpi()],
        earnings_events=[
            EarningsEvent(
                symbol="MU",
                report_date=date(2026, 9, 30),
                session="after_market",
                status="expected",
                source="Alpha Vantage",
            )
        ],
    )
    text = summarize(report)

    assert "Weekly Market Monitor" in text
    assert "Sep 21" in text and "Sep 27" in text
    assert "CPI" in text
    assert "Tue 8:30 AM EDT" in text
    assert "MU" in text
    assert "After Market (Expected)" in text
    assert "All data refreshed successfully." in text


def test_summary_reports_warnings_instead_of_success():
    report = build(warnings=["BLS refresh failed"])
    text = summarize(report)
    assert "All data refreshed successfully." not in text
    assert "BLS refresh failed" in text


def test_summary_handles_an_empty_week():
    text = summarize(build())
    assert "No events at the critical level this week." in text
    assert "No monitored earnings in the lookahead window." in text


def test_summary_only_promotes_events_at_the_critical_level():
    ppi = make_macro_event(
        "Producer Price Index", datetime(2026, 9, 23, 8, 30, tzinfo=EASTERN)
    ).model_copy(update={"canonical_name": "PPI", "importance": 4})

    text = summarize(build(macro_events=[cpi(), ppi]))
    assert "CPI" in text
    assert "PPI" not in text


def test_json_export_matches_the_documented_shape(tmp_path):
    report = build(
        macro_events=[cpi()],
        provider_statuses=[ProviderStatus(provider="BLS", status="ok")],
    )
    path = write_json(report, str(tmp_path / "out" / "current_report.json"))

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["generated_at"].startswith("2026-09-20T10:00:00")
    assert payload["macro_start"] == "2026-09-21"
    assert payload["macro_end"] == "2026-09-27"
    assert payload["earnings_start"] == "2026-09-20"
    assert payload["earnings_end"] == "2026-10-20"
    assert payload["macro_events"][0]["canonical_name"] == "CPI"
    assert payload["macro_events"][0]["importance"] == 5
    assert payload["earnings_events"] == []
    assert payload["warnings"] == []


def test_json_export_is_skipped_when_unconfigured():
    assert write_json(build(), None) is None


def test_all_providers_ok_flag():
    healthy = build(provider_statuses=[ProviderStatus(provider="BLS", status="ok")])
    degraded = build(
        provider_statuses=[ProviderStatus(provider="BLS", status="warning")]
    )
    assert healthy.all_providers_ok is True
    assert degraded.all_providers_ok is False
