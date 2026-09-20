"""Event name normalization (spec section 15)."""

import pytest

from market_monitor.config import load_config
from market_monitor.services.normalizer import EventNormalizer, normalize_text

from .test_config import REPO_ROOT


@pytest.fixture
def normalizer(config):
    return EventNormalizer(config.macro.events)


def test_alias_maps_to_canonical(normalizer):
    assert normalizer.normalize("Consumer Price Index") == "CPI"
    assert normalizer.normalize("Producer Price Index") == "PPI"


def test_canonical_name_is_its_own_alias(normalizer):
    assert normalizer.normalize("CPI") == "CPI"
    assert normalizer.normalize("Initial Jobless Claims") == "Initial Jobless Claims"


def test_matching_ignores_case_and_punctuation(normalizer):
    assert normalizer.normalize("consumer price index") == "CPI"
    assert normalizer.normalize("  CONSUMER   PRICE-INDEX  ") == "CPI"


def test_trailing_reference_period_is_stripped(normalizer):
    assert (
        normalizer.normalize("Gross Domestic Product, 2nd Quarter 2026") == "GDP"
    )


def test_parentheticals_are_stripped(normalizer):
    assert (
        normalizer.normalize("Gross Domestic Product (Advance Estimate)") == "GDP"
    )


def test_period_and_parenthetical_together(normalizer):
    assert (
        normalizer.normalize(
            "Gross Domestic Product, 4th Quarter and Year 2026 (Advance Estimate)"
        )
        == "GDP"
    )


def test_unknown_release_returns_none(normalizer):
    assert normalizer.normalize("Quarterly Services Survey") is None
    assert normalizer.normalize("") is None


def test_related_releases_are_not_confused_for_the_headline(normalizer):
    # A substring rule would file these as GDP. They are different
    # releases and must stay unmatched.
    assert (
        normalizer.normalize("Gross Domestic Product by State and Personal Income by State")
        is None
    )
    assert normalizer.normalize("GDP by County and Personal Income by County") is None


def test_disabled_events_still_normalize(normalizer):
    # Enablement is the service's decision, not the normalizer's.
    assert (
        normalizer.normalize("Advance Monthly Sales for Retail and Food Services")
        == "Retail Sales"
    )


def test_normalize_text_collapses_punctuation():
    assert (
        normalize_text("Durable Goods--Manufacturers' Shipments, Inventories")
        == "durable goods manufacturers shipments inventories"
    )


def test_shipped_config_matches_real_agency_titles():
    """The aliases in config.yaml resolve the titles the agencies publish."""
    config = load_config(REPO_ROOT / "config" / "config.yaml")
    normalizer = EventNormalizer(config.macro.events)

    cases = {
        "Consumer Price Index": "CPI",
        "Producer Price Index": "PPI",
        "Employment Situation": "NFP",
        "Job Openings and Labor Turnover Survey": "JOLTS",
        "Employment Cost Index": "Employment Cost Index",
        "Personal Income and Outlays, August 2026": "PCE",
        "Gross Domestic Product, 2nd Quarter 2026 (Second Estimate)": "GDP",
        "GDP (Advance Estimate), 3rd Quarter 2026": "GDP",
        "GDP (Second Estimate) and Corporate Profits, 3rd Quarter 2026": "GDP",
        "Advance Monthly Sales for Retail and Food Services": "Retail Sales",
        "Advance Report on Durable Goods--Manufacturers' Shipments, "
        "Inventories, and Orders": "Durable Goods Orders",
        "New Residential Construction (Building Permits, Housing Starts, "
        "and Housing Completions)": "Housing Starts",
        "Federal Reserve Interest Rate Decision": "FOMC Rate Decision",
        "FOMC Statement": "FOMC Statement",
        "FOMC Minutes": "FOMC Minutes",
    }

    for raw, expected in cases.items():
        assert normalizer.normalize(raw) == expected, raw


def test_shipped_config_ignores_minor_releases():
    config = load_config(REPO_ROOT / "config" / "config.yaml")
    normalizer = EventNormalizer(config.macro.events)

    for raw in [
        "County Employment and Wages",
        "Employee Tenure",
        "Business Formation Statistics",
        "Construction Spending (Construction Put in Place)",
        "Gross Domestic Product by State and Personal Income by State",
    ]:
        assert normalizer.normalize(raw) is None, raw
