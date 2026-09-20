"""Assemble the WeeklyReport and its derived outputs."""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Sequence

from ..models.earnings_event import EarningsEvent
from ..models.macro_event import MacroEvent
from ..models.provider_status import ProviderStatus
from ..models.weekly_report import WeeklyReport
from ..utils.dates import format_local

logger = logging.getLogger(__name__)

IMPORTANCE_LABELS = {
    5: "Critical",
    4: "Very Important",
    3: "Important",
    2: "Moderate",
    1: "Low",
}


def stars(importance: int) -> str:
    return "★" * max(0, min(5, importance))


def importance_label(importance: int) -> str:
    return IMPORTANCE_LABELS.get(importance, "Unrated")


def build_weekly_report(
    generated_at: datetime,
    macro_start: date,
    macro_end: date,
    earnings_start: date,
    earnings_end: date,
    macro_events: Sequence[MacroEvent],
    earnings_events: Sequence[EarningsEvent],
    warnings: Sequence[str],
    provider_statuses: Sequence[ProviderStatus],
) -> WeeklyReport:
    return WeeklyReport(
        generated_at=generated_at,
        macro_start=macro_start,
        macro_end=macro_end,
        earnings_start=earnings_start,
        earnings_end=earnings_end,
        macro_events=list(macro_events),
        earnings_events=list(earnings_events),
        warnings=list(warnings),
        provider_statuses=list(provider_statuses),
    )


def write_json(report: WeeklyReport, path: Optional[str]) -> Optional[Path]:
    """Export the report so other front-ends can consume it (spec section 57)."""
    if not path:
        return None

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(report.model_dump_json())
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    logger.info("Wrote report JSON to %s", target)
    return target


def summarize(report: WeeklyReport, critical_importance: int = 5) -> str:
    """Compact text summary (spec section 43).

    Every value here is read off the structured report; nothing is
    inferred or invented.
    """
    lines: List[str] = ["Weekly Market Monitor"]
    lines.append(
        "{} – {}".format(
            report.macro_start.strftime("%b %d"),
            report.macro_end.strftime("%b %d"),
        )
    )
    lines.append("")

    critical = [
        event
        for event in report.macro_events
        if event.importance >= critical_importance
    ]
    if critical:
        lines.append("Critical events:")
        lines.append("")
        for event in critical:
            lines.append(format_local(event.datetime_local, "%a %I:%M %p %Z"))
            lines.append(event.canonical_name)
            lines.append("")
    else:
        lines.append("No events at the critical level this week.")
        lines.append("")

    if report.earnings_events:
        lines.append("Upcoming earnings:")
        lines.append("")
        for event in report.earnings_events:
            lines.append(event.symbol)
            lines.append(event.report_date.strftime("%b %d"))
            lines.append("{} ({})".format(event.session_label, event.status_label))
            lines.append("")
    else:
        lines.append("No monitored earnings in the lookahead window.")
        lines.append("")

    if report.warnings:
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append("- {}".format(warning))
    else:
        lines.append("All data refreshed successfully.")

    return "\n".join(lines).rstrip() + "\n"
