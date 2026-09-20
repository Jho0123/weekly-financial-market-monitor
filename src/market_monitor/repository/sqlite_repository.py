"""SQLite persistence.

Two jobs:

1. Keep the last good snapshot from every provider, so a failed refresh
   degrades to stale-but-labelled data instead of an empty dashboard
   (spec section 36).
2. Keep a run log, so an unattended Sunday run can be debugged after the
   fact (spec section 35).

Snapshots are written per provider and only after a *successful* fetch,
which is what makes the fallback safe: a failure never overwrites the
rows it would later need to read.

Aware datetimes are stored as ISO-8601 strings. SQLite's native datetime
handling drops the offset, and the offset is exactly what a market
calendar cannot afford to lose.
"""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    select,
)
from sqlalchemy.orm import Session, declarative_base

from ..models.earnings_event import EarningsEvent
from ..models.macro_event import MacroEvent
from ..models.weekly_report import WeeklyReport

logger = logging.getLogger(__name__)

Base = declarative_base()

# Number of historical snapshots to keep per provider.
SNAPSHOT_HISTORY = 4


class MacroEventRow(Base):
    __tablename__ = "macro_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_at = Column(String, nullable=False, index=True)

    event_id = Column(String, nullable=False)
    canonical_name = Column(String, nullable=False)
    raw_name = Column(String, nullable=False)
    country = Column(String, nullable=False)
    datetime_utc = Column(String, nullable=False)
    datetime_local = Column(String, nullable=False)
    local_date = Column(String, nullable=False, index=True)
    importance = Column(Integer, nullable=False, default=0)
    actual = Column(String)
    forecast = Column(String)
    previous = Column(String)
    source = Column(String, nullable=False, index=True)
    source_url = Column(String)
    retrieved_at = Column(String, nullable=False)


class EarningsEventRow(Base):
    __tablename__ = "earnings_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_at = Column(String, nullable=False, index=True)

    symbol = Column(String, nullable=False, index=True)
    company_name = Column(String)
    report_date = Column(String, nullable=False, index=True)
    session = Column(String, nullable=False)
    estimated_eps = Column(Float)
    status = Column(String, nullable=False)
    source = Column(String, nullable=False)
    source_url = Column(String)
    retrieved_at = Column(String, nullable=False)


class RefreshRunRow(Base):
    __tablename__ = "refresh_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime)

    macro_status = Column(String)
    earnings_status = Column(String)

    macro_event_count = Column(Integer, default=0)
    earnings_event_count = Column(Integer, default=0)

    error_message = Column(Text)


class ReportRow(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_at = Column(String, nullable=False, index=True)
    payload = Column(Text, nullable=False)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _as_text(value) -> Optional[str]:
    return None if value is None else str(value)


class SqliteRepository:
    def __init__(self, path: str = "./data/market_monitor.db", echo: bool = False):
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            url = "sqlite:///{}".format(Path(path).expanduser())
        else:
            url = "sqlite://"

        self.engine = create_engine(url, echo=echo, future=True)
        Base.metadata.create_all(self.engine)

    # ---------------------------------------------------------------- macro

    def save_macro_snapshot(self, source: str, events: List[MacroEvent]) -> None:
        snapshot_at = _iso(datetime.now(timezone.utc))

        with Session(self.engine) as session:
            for event in events:
                session.add(
                    MacroEventRow(
                        snapshot_at=snapshot_at,
                        event_id=event.event_id,
                        canonical_name=event.canonical_name,
                        raw_name=event.raw_name,
                        country=event.country,
                        datetime_utc=_iso(event.datetime_utc),
                        datetime_local=_iso(event.datetime_local),
                        local_date=event.datetime_local.date().isoformat(),
                        importance=event.importance,
                        actual=_as_text(event.actual),
                        forecast=_as_text(event.forecast),
                        previous=_as_text(event.previous),
                        source=source,
                        source_url=event.source_url,
                        retrieved_at=_iso(event.retrieved_at),
                    )
                )
            session.commit()

        self._prune_macro(source)

    def load_macro_snapshot(
        self, source: str, start: date, end: date
    ) -> Optional[Tuple[List[MacroEvent], datetime]]:
        """Most recent stored snapshot for ``source``, clipped to the window."""
        with Session(self.engine) as session:
            latest = session.execute(
                select(MacroEventRow.snapshot_at)
                .where(MacroEventRow.source == source)
                .order_by(MacroEventRow.snapshot_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest is None:
                return None

            rows = session.execute(
                select(MacroEventRow)
                .where(MacroEventRow.source == source)
                .where(MacroEventRow.snapshot_at == latest)
                .where(MacroEventRow.local_date >= start.isoformat())
                .where(MacroEventRow.local_date <= end.isoformat())
            ).scalars().all()

            events = [self._row_to_macro(row) for row in rows]

        return events, _parse_iso(latest)

    @staticmethod
    def _row_to_macro(row: MacroEventRow) -> MacroEvent:
        return MacroEvent(
            event_id=row.event_id,
            canonical_name=row.canonical_name,
            raw_name=row.raw_name,
            country=row.country,
            datetime_utc=_parse_iso(row.datetime_utc),
            datetime_local=_parse_iso(row.datetime_local),
            importance=row.importance or 0,
            actual=row.actual,
            forecast=row.forecast,
            previous=row.previous,
            source=row.source,
            source_url=row.source_url,
            retrieved_at=_parse_iso(row.retrieved_at),
        )

    def _prune_macro(self, source: str) -> None:
        with Session(self.engine) as session:
            keep = session.execute(
                select(MacroEventRow.snapshot_at)
                .where(MacroEventRow.source == source)
                .distinct()
                .order_by(MacroEventRow.snapshot_at.desc())
                .limit(SNAPSHOT_HISTORY)
            ).scalars().all()

            if len(keep) < SNAPSHOT_HISTORY:
                return

            session.execute(
                delete(MacroEventRow)
                .where(MacroEventRow.source == source)
                .where(MacroEventRow.snapshot_at.notin_(keep))
            )
            session.commit()

    # ------------------------------------------------------------- earnings

    def save_earnings_snapshot(self, events: List[EarningsEvent]) -> None:
        snapshot_at = _iso(datetime.now(timezone.utc))

        with Session(self.engine) as session:
            for event in events:
                session.add(
                    EarningsEventRow(
                        snapshot_at=snapshot_at,
                        symbol=event.symbol,
                        company_name=event.company_name,
                        report_date=event.report_date.isoformat(),
                        session=event.session,
                        estimated_eps=event.estimated_eps,
                        status=event.status,
                        source=event.source,
                        source_url=event.source_url,
                        retrieved_at=_iso(event.retrieved_at),
                    )
                )
            session.commit()

        self._prune_earnings()

    def load_earnings_snapshot(
        self, start: date, end: date
    ) -> Optional[Tuple[List[EarningsEvent], datetime]]:
        with Session(self.engine) as session:
            latest = session.execute(
                select(EarningsEventRow.snapshot_at)
                .order_by(EarningsEventRow.snapshot_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if latest is None:
                return None

            rows = session.execute(
                select(EarningsEventRow)
                .where(EarningsEventRow.snapshot_at == latest)
                .where(EarningsEventRow.report_date >= start.isoformat())
                .where(EarningsEventRow.report_date <= end.isoformat())
            ).scalars().all()

            events = [
                EarningsEvent(
                    symbol=row.symbol,
                    company_name=row.company_name,
                    report_date=date.fromisoformat(row.report_date),
                    session=row.session,
                    estimated_eps=row.estimated_eps,
                    status=row.status,
                    source=row.source,
                    source_url=row.source_url,
                    retrieved_at=_parse_iso(row.retrieved_at),
                )
                for row in rows
            ]

        return events, _parse_iso(latest)

    def _prune_earnings(self) -> None:
        with Session(self.engine) as session:
            keep = session.execute(
                select(EarningsEventRow.snapshot_at)
                .distinct()
                .order_by(EarningsEventRow.snapshot_at.desc())
                .limit(SNAPSHOT_HISTORY)
            ).scalars().all()

            if len(keep) < SNAPSHOT_HISTORY:
                return

            session.execute(
                delete(EarningsEventRow).where(
                    EarningsEventRow.snapshot_at.notin_(keep)
                )
            )
            session.commit()

    # -------------------------------------------------------------- reports

    def save_report(self, report: WeeklyReport) -> None:
        payload = report.model_dump_json()
        with Session(self.engine) as session:
            session.add(
                ReportRow(
                    generated_at=_iso(report.generated_at),
                    payload=payload,
                )
            )
            session.commit()

    def load_latest_report(self) -> Optional[WeeklyReport]:
        with Session(self.engine) as session:
            row = session.execute(
                select(ReportRow).order_by(ReportRow.id.desc()).limit(1)
            ).scalar_one_or_none()

            if row is None:
                return None

            try:
                return WeeklyReport.model_validate(json.loads(row.payload))
            except Exception as exc:  # noqa: BLE001
                logger.error("Stored report could not be read back: %s", exc)
                return None

    # ----------------------------------------------------------------- runs

    def start_run(self, started_at: datetime) -> int:
        with Session(self.engine) as session:
            run = RefreshRunRow(started_at=started_at)
            session.add(run)
            session.commit()
            return run.id

    def finish_run(
        self,
        run_id: int,
        completed_at: datetime,
        macro_status: str,
        earnings_status: str,
        macro_event_count: int,
        earnings_event_count: int,
        error_message: Optional[str] = None,
    ) -> None:
        with Session(self.engine) as session:
            run = session.get(RefreshRunRow, run_id)
            if run is None:
                return
            run.completed_at = completed_at
            run.macro_status = macro_status
            run.earnings_status = earnings_status
            run.macro_event_count = macro_event_count
            run.earnings_event_count = earnings_event_count
            run.error_message = error_message
            session.commit()

    def recent_runs(self, limit: int = 10) -> List[RefreshRunRow]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(RefreshRunRow).order_by(RefreshRunRow.id.desc()).limit(limit)
            ).scalars().all()
            session.expunge_all()
            return rows
