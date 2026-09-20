"""Macro filtering, importance and deduplication (spec section 53)."""

from datetime import datetime


from market_monitor.providers.macro.composite import CompositeMacroProvider
from market_monitor.services.macro_service import MacroService
from market_monitor.services.normalizer import EventNormalizer

from .conftest import EASTERN, TORONTO, StubMacroProvider, make_macro_event

REFERENCE = datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO)  # Sunday


def at(day: int, hour: int = 8, minute: int = 30) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=EASTERN)


def build_service(config, providers):
    return MacroService(
        provider=CompositeMacroProvider(providers),
        normalizer=EventNormalizer(config.macro.events),
        config=config,
    )


def run(config, events):
    service = build_service(config, [StubMacroProvider("BLS", events)])
    return service.fetch_week(REFERENCE)


def test_configured_events_are_included_with_user_importance(config):
    events, _, _ = run(
        config,
        [
            make_macro_event("Consumer Price Index", at(22)),
            make_macro_event("Producer Price Index", at(23)),
            make_macro_event("Initial Jobless Claims", at(24)),
        ],
    )

    assert [(e.canonical_name, e.importance) for e in events] == [
        ("CPI", 5),
        ("PPI", 4),
        ("Initial Jobless Claims", 3),
    ]


def test_importance_below_minimum_is_excluded(config):
    # Consumer Confidence is configured at 2; the minimum is 3.
    events, _, _ = run(config, [make_macro_event("Consumer Confidence", at(22))])
    assert events == []


def test_unknown_event_is_excluded(config):
    events, _, _ = run(config, [make_macro_event("Quarterly Services Survey", at(22))])
    assert events == []


def test_disabled_event_is_excluded(config):
    events, _, _ = run(
        config,
        [make_macro_event("Advance Monthly Sales for Retail and Food Services", at(22))],
    )
    assert events == []


def test_events_outside_the_window_are_excluded(config):
    events, _, _ = run(
        config,
        [
            make_macro_event("Consumer Price Index", at(20)),  # before Monday
            make_macro_event("Consumer Price Index", at(22)),  # inside
            make_macro_event("Consumer Price Index", at(28)),  # after Sunday
        ],
    )
    assert [e.datetime_local.date().day for e in events] == [22]


def test_events_are_sorted_chronologically(config):
    events, _, _ = run(
        config,
        [
            make_macro_event("Consumer Price Index", at(25)),
            make_macro_event("Producer Price Index", at(22)),
            make_macro_event("Initial Jobless Claims", at(23)),
        ],
    )
    assert [e.datetime_local for e in events] == sorted(
        e.datetime_local for e in events
    )


def test_identical_events_from_two_sources_deduplicate(config):
    moment = at(22)
    service = build_service(
        config,
        [
            StubMacroProvider("BLS", [make_macro_event("Consumer Price Index", moment, "BLS")]),
            StubMacroProvider(
                "Secondary", [make_macro_event("CPI", moment, "Secondary")]
            ),
        ],
    )
    events, _, warnings = service.fetch_week(REFERENCE)

    assert len(events) == 1
    # The agency that owns the release wins.
    assert events[0].source == "BLS"
    assert warnings == []


def test_conflicting_times_keep_both_and_warn(config):
    """Spec section 65: never silently choose between disagreeing sources."""
    service = build_service(
        config,
        [
            StubMacroProvider(
                "BLS", [make_macro_event("Consumer Price Index", at(22, 8, 30), "BLS")]
            ),
            StubMacroProvider(
                "Secondary",
                [make_macro_event("CPI", at(22, 10, 0), "Secondary")],
            ),
        ],
    )
    events, _, warnings = service.fetch_week(REFERENCE)

    assert len(events) == 2
    assert len(warnings) == 1
    assert "schedule mismatch" in warnings[0]
    assert "BLS" in warnings[0] and "Secondary" in warnings[0]


def test_other_countries_are_filtered_out(config):
    events, _, _ = run(
        config,
        [
            make_macro_event("Consumer Price Index", at(22), country="Canada"),
            make_macro_event("Consumer Price Index", at(23)),
        ],
    )
    assert len(events) == 1
    assert events[0].country == "United States"


def test_raising_the_minimum_narrows_the_report(config):
    config.macro.minimum_importance = 5
    events, _, _ = run(
        config,
        [
            make_macro_event("Consumer Price Index", at(22)),  # 5
            make_macro_event("Producer Price Index", at(23)),  # 4
        ],
    )
    assert [e.canonical_name for e in events] == ["CPI"]


def test_event_id_reflects_the_canonical_name(config):
    events, _, _ = run(config, [make_macro_event("Consumer Price Index", at(22))])
    assert events[0].event_id == "united-states-cpi-2026-09-22T1230Z"


def test_unmatched_names_are_reportable(config):
    service = build_service(config, [])
    raw = [
        make_macro_event("Consumer Price Index", at(22)),
        make_macro_event("Quarterly Services Survey", at(23)),
    ]
    assert service.unmatched_names(raw) == ["Quarterly Services Survey"]
