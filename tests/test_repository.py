"""SQLite persistence (spec sections 32-35)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from market_monitor.models.earnings_event import EarningsEvent
from market_monitor.models.provider_status import ProviderStatus
from market_monitor.models.weekly_report import WeeklyReport
from market_monitor.repository.sqlite_repository import (
    SNAPSHOT_HISTORY,
    SqliteRepository,
)

from .conftest import EASTERN, TORONTO, make_macro_event

CPI_AT = datetime(2026, 9, 22, 8, 30, tzinfo=EASTERN)


@pytest.fixture
def repository(tmp_path):
    return SqliteRepository(str(tmp_path / "db.sqlite"))


def test_empty_repository_has_nothing_cached(repository):
    assert repository.load_macro_snapshot("BLS", date(2026, 1, 1), date(2026, 12, 31)) is None
    assert repository.load_earnings_snapshot(date(2026, 1, 1), date(2026, 12, 31)) is None
    assert repository.load_latest_report() is None


def test_macro_round_trip_preserves_the_offset(repository):
    event = make_macro_event("Consumer Price Index", CPI_AT)
    repository.save_macro_snapshot("BLS", [event])

    events, cached_at = repository.load_macro_snapshot(
        "BLS", date(2026, 9, 21), date(2026, 9, 27)
    )

    assert len(events) == 1
    restored = events[0]
    assert restored.datetime_local == CPI_AT
    assert restored.datetime_local.utcoffset() == CPI_AT.utcoffset()
    assert restored.raw_name == "Consumer Price Index"
    assert cached_at.tzinfo is not None


def test_macro_snapshot_is_scoped_per_provider(repository):
    repository.save_macro_snapshot("BLS", [make_macro_event("Consumer Price Index", CPI_AT)])
    repository.save_macro_snapshot("BEA", [make_macro_event("Gross Domestic Product", CPI_AT)])

    bls, _ = repository.load_macro_snapshot("BLS", date(2026, 9, 21), date(2026, 9, 27))
    assert [e.raw_name for e in bls] == ["Consumer Price Index"]


def test_only_the_newest_snapshot_is_returned(repository):
    repository.save_macro_snapshot("BLS", [make_macro_event("Consumer Price Index", CPI_AT)])
    repository.save_macro_snapshot(
        "BLS",
        [
            make_macro_event("Consumer Price Index", CPI_AT),
            make_macro_event("Producer Price Index", CPI_AT + timedelta(days=1)),
        ],
    )

    events, _ = repository.load_macro_snapshot(
        "BLS", date(2026, 9, 21), date(2026, 9, 27)
    )
    assert len(events) == 2


def test_snapshots_are_clipped_to_the_requested_window(repository):
    repository.save_macro_snapshot(
        "BLS",
        [
            make_macro_event("Consumer Price Index", CPI_AT),
            make_macro_event("Consumer Price Index", CPI_AT + timedelta(days=60)),
        ],
    )
    events, _ = repository.load_macro_snapshot(
        "BLS", date(2026, 9, 21), date(2026, 9, 27)
    )
    assert len(events) == 1


def test_old_snapshots_are_pruned(repository):
    for index in range(SNAPSHOT_HISTORY + 4):
        repository.save_macro_snapshot(
            "BLS", [make_macro_event("Consumer Price Index", CPI_AT)]
        )

    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from market_monitor.repository.sqlite_repository import MacroEventRow

    with Session(repository.engine) as session:
        distinct = session.execute(
            select(MacroEventRow.snapshot_at).distinct()
        ).scalars().all()

    assert len(distinct) <= SNAPSHOT_HISTORY


def test_earnings_round_trip(repository):
    repository.save_earnings_snapshot(
        [
            EarningsEvent(
                symbol="MU",
                company_name="MICRON TECHNOLOGY INCORPORATED",
                report_date=date(2026, 9, 30),
                session="after_market",
                estimated_eps=31.24,
                status="expected",
                source="Alpha Vantage",
            )
        ]
    )

    events, cached_at = repository.load_earnings_snapshot(
        date(2026, 9, 20), date(2026, 10, 20)
    )
    assert len(events) == 1
    assert events[0].symbol == "MU"
    assert events[0].estimated_eps == pytest.approx(31.24)
    assert events[0].session_label == "After Market"
    assert cached_at is not None


def test_report_round_trip(repository):
    report = WeeklyReport(
        generated_at=datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO),
        macro_start=date(2026, 9, 21),
        macro_end=date(2026, 9, 27),
        earnings_start=date(2026, 9, 20),
        earnings_end=date(2026, 10, 20),
        macro_events=[make_macro_event("Consumer Price Index", CPI_AT)],
        warnings=["something was stale"],
        provider_statuses=[ProviderStatus(provider="BLS", status="ok")],
    )
    repository.save_report(report)

    loaded = repository.load_latest_report()
    assert loaded is not None
    assert loaded.generated_at == report.generated_at
    assert loaded.macro_events[0].datetime_local == CPI_AT
    assert loaded.warnings == ["something was stale"]
    assert loaded.provider_statuses[0].provider == "BLS"


def test_latest_report_wins(repository):
    base = dict(
        macro_start=date(2026, 9, 21),
        macro_end=date(2026, 9, 27),
        earnings_start=date(2026, 9, 20),
        earnings_end=date(2026, 10, 20),
    )
    repository.save_report(
        WeeklyReport(generated_at=datetime(2026, 9, 13, 10, 0, tzinfo=TORONTO), **base)
    )
    repository.save_report(
        WeeklyReport(generated_at=datetime(2026, 9, 20, 10, 0, tzinfo=TORONTO), **base)
    )

    assert repository.load_latest_report().generated_at.day == 20


def test_run_log_records_outcome(repository):
    started = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)
    run_id = repository.start_run(started)
    repository.finish_run(
        run_id=run_id,
        completed_at=started + timedelta(seconds=9),
        macro_status="warning",
        earnings_status="ok",
        macro_event_count=4,
        earnings_event_count=1,
        error_message="BLS refresh failed",
    )

    runs = repository.recent_runs()
    assert len(runs) == 1
    assert runs[0].macro_status == "warning"
    assert runs[0].macro_event_count == 4
    assert runs[0].error_message == "BLS refresh failed"
    assert runs[0].completed_at is not None


def test_database_file_is_created_with_its_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "db.sqlite"
    SqliteRepository(str(target))
    assert target.exists()
