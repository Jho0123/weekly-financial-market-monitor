from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from market_monitor.config import Config
from market_monitor.models.macro_event import MacroEvent

FIXTURES = Path(__file__).parent / "fixtures"

TORONTO = ZoneInfo("America/Toronto")
EASTERN = ZoneInfo("America/New_York")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def config() -> Config:
    """A small config that does not depend on the shipped YAML."""
    return Config.model_validate(
        {
            "app": {"timezone": "America/Toronto"},
            "macro": {
                "minimum_importance": 3,
                "country": ["United States"],
                "events": {
                    "CPI": {
                        "importance": 5,
                        "aliases": ["Consumer Price Index"],
                    },
                    "PPI": {
                        "importance": 4,
                        "aliases": ["Producer Price Index"],
                    },
                    "Initial Jobless Claims": {"importance": 3},
                    "Consumer Confidence": {"importance": 2},
                    "GDP": {
                        "importance": 4,
                        "aliases": ["Gross Domestic Product"],
                    },
                    "Retail Sales": {
                        "enabled": False,
                        "importance": 4,
                        "aliases": ["Advance Monthly Sales for Retail and Food Services"],
                    },
                },
            },
            "earnings": {"symbols": ["NVDA", "mu"], "lookahead_days": 30},
        }
    )


def make_macro_event(
    raw_name: str,
    local: datetime,
    source: str = "BLS",
    country: str = "United States",
) -> MacroEvent:
    return MacroEvent(
        event_id=MacroEvent.build_event_id(country, raw_name, local),
        canonical_name=raw_name,
        raw_name=raw_name,
        country=country,
        datetime_utc=local.astimezone(timezone.utc),
        datetime_local=local,
        source=source,
    )


class StubMacroProvider:
    """A macro provider that returns fixed events or raises."""

    def __init__(self, name, events=None, error=None):
        self.name = name
        self.events = events or []
        self.error = error
        self.calls = 0

    def get_events(self, start, end):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [
            event
            for event in self.events
            if start <= event.datetime_local.date() <= end
        ]


class StubEarningsProvider:
    def __init__(self, name="Stub", events=None, error=None):
        self.name = name
        self.events = events or []
        self.error = error
        self.calls = 0

    def get_earnings(self, symbols, start, end):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.events)
