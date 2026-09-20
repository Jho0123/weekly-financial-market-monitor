"""Dashboard rendering.

Runs the real Streamlit script against a temporary config and database,
so a template mistake fails here rather than on a Sunday morning.
"""

from datetime import date, datetime

import pytest

from market_monitor.models.earnings_event import EarningsEvent
from market_monitor.models.provider_status import ProviderStatus
from market_monitor.models.weekly_report import WeeklyReport
from market_monitor.repository.sqlite_repository import SqliteRepository

from .conftest import EASTERN, TORONTO, make_macro_event
from .test_config import REPO_ROOT

APP = str(REPO_ROOT / "src" / "market_monitor" / "dashboard" / "app.py")

pytest.importorskip("streamlit.testing.v1")


CONFIG_TEMPLATE = """
app:
  timezone: "America/Toronto"
storage:
  path: "{db}"
macro:
  minimum_importance: 3
  events:
    CPI:
      importance: 5
earnings:
  symbols: ["MU"]
dashboard:
  title: "Test Monitor"
  sections:
    tradingview_widget: false
output:
  json_path: null
logging:
  file: null
"""


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG_TEMPLATE.format(db=db), encoding="utf-8")
    monkeypatch.setenv("MARKET_MONITOR_CONFIG", str(config))
    return SqliteRepository(str(db))


def run_app():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def sample_report(**overrides):
    cpi = make_macro_event(
        "Consumer Price Index", datetime(2026, 9, 22, 8, 30, tzinfo=EASTERN)
    ).model_copy(update={"canonical_name": "CPI", "importance": 5})

    defaults = dict(
        generated_at=datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO),
        macro_start=date(2026, 9, 21),
        macro_end=date(2026, 9, 27),
        earnings_start=date(2026, 9, 20),
        earnings_end=date(2026, 10, 20),
        macro_events=[cpi],
        earnings_events=[
            EarningsEvent(
                symbol="MU",
                company_name="MICRON TECHNOLOGY INCORPORATED",
                report_date=date(2026, 9, 30),
                session="after_market",
                status="expected",
                estimated_eps=31.24,
                source="Alpha Vantage",
            )
        ],
        warnings=[],
        provider_statuses=[
            ProviderStatus(provider="BLS", status="ok", event_count=1),
        ],
    )
    defaults.update(overrides)
    return WeeklyReport(**defaults)


def test_dashboard_prompts_when_no_report_exists(app_env):
    app = run_app()
    assert any("No report has been generated yet" in i.value for i in app.info)


def test_dashboard_renders_macro_and_earnings_together(app_env):
    app_env.save_report(sample_report())
    app = run_app()

    headings = [s.value for s in app.subheader]
    assert "Important events this week" in headings
    assert "Upcoming earnings - next 30 days" in headings
    assert "Data status" in headings

    assert app.title[0].value == "Test Monitor"
    assert any("All data refreshed successfully." in s.value for s in app.success)


def test_dashboard_shows_warnings_for_stale_data(app_env):
    app_env.save_report(
        sample_report(
            warnings=[
                "BLS refresh failed. Showing previously cached BLS calendar "
                "data from Sat 11:42 PM EDT."
            ],
            provider_statuses=[
                ProviderStatus(
                    provider="BLS",
                    status="warning",
                    error="503",
                    from_cache=True,
                    last_success=datetime(2026, 9, 19, 23, 42, tzinfo=TORONTO),
                )
            ],
        )
    )
    app = run_app()

    assert app.warning
    assert "Showing previously cached" in app.warning[0].value
    assert not app.success


def test_tradingview_section_respects_the_config_toggle(app_env, tmp_path):
    app_env.save_report(sample_report())
    app = run_app()
    assert "TradingView economic calendar" not in [s.value for s in app.subheader]


def test_dashboard_reports_a_bad_config(tmp_path, monkeypatch):
    bad = tmp_path / "config.yaml"
    bad.write_text('app:\n  timezone: "Mars/Olympus"\n', encoding="utf-8")
    monkeypatch.setenv("MARKET_MONITOR_CONFIG", str(bad))

    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(APP, default_timeout=60)
    app.run()

    assert app.error
    assert "Invalid config" in app.error[0].value
