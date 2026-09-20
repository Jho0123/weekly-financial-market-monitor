"""Streamlit dashboard.

Consumes a stored WeeklyReport and nothing else. Which agency produced a
row is a field on the model, not a branch in this code.
"""

import argparse
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Allow `streamlit run src/market_monitor/dashboard/app.py` without an install.
if __package__ in (None, ""):  # pragma: no cover - script-mode shim
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from market_monitor.config import ConfigError, load_config  # noqa: E402
from market_monitor.dashboard import (  # noqa: E402
    earnings_section,
    macro_section,
    tradingview_widget,
)
from market_monitor.models.provider_status import (  # noqa: E402
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
)
from market_monitor.repository.sqlite_repository import SqliteRepository  # noqa: E402
from market_monitor.services.report_service import summarize  # noqa: E402
from market_monitor.utils.dates import format_local  # noqa: E402

STATUS_ICONS = {
    STATUS_OK: "🟢",
    STATUS_WARNING: "🟡",
    STATUS_ERROR: "🔴",
}


def _config_path():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=None)
    known, _ = parser.parse_known_args()
    return known.config


def render_data_status(report, tz) -> None:
    st.subheader("Data status")

    columns = st.columns(max(1, len(report.provider_statuses)))
    for column, status in zip(columns, report.provider_statuses):
        icon = STATUS_ICONS.get(status.status, "⚪")
        column.metric(
            label="{} {}".format(icon, status.provider),
            value=status.label,
            delta="{} events".format(status.event_count),
            delta_color="off",
        )
        if status.last_success:
            column.caption(
                "Last success: {}".format(
                    format_local(status.last_success.astimezone(tz))
                )
            )
        else:
            column.caption("Last success: never")
        if status.error:
            column.caption("Error: {}".format(status.error))

    st.caption(
        "Last attempted refresh: {}".format(
            format_local(report.generated_at.astimezone(tz))
        )
    )


def render_warnings(report) -> None:
    if not report.warnings:
        st.success("All data refreshed successfully.")
        return
    for warning in report.warnings:
        st.warning(warning)


def main() -> None:
    load_dotenv()

    try:
        config = load_config(_config_path())
    except ConfigError as exc:
        st.set_page_config(page_title="Market Monitor", layout="wide")
        st.error(str(exc))
        st.stop()
        return

    st.set_page_config(
        page_title=config.dashboard.title, page_icon="📈", layout="wide"
    )

    tz = config.app.tzinfo
    sections = config.dashboard.sections

    st.title(config.dashboard.title)

    repository = SqliteRepository(config.storage.path)
    report = repository.load_latest_report()

    with st.sidebar:
        st.header("Refresh")
        st.caption(
            "Scheduled: {} at {} {}".format(
                config.schedule.day_of_week.title(),
                config.schedule.time,
                config.app.timezone,
            )
        )
        if st.button("Refresh now", width="stretch"):
            from market_monitor.main import refresh

            with st.spinner("Fetching official calendars..."):
                try:
                    refresh(config)
                except Exception as exc:  # noqa: BLE001
                    st.error("Refresh failed: {}".format(exc))
                else:
                    st.rerun()

        st.divider()
        st.caption("Watchlist: {}".format(", ".join(config.earnings.symbols)))
        st.caption("Minimum importance: {}".format(config.macro.minimum_importance))

    if report is None:
        st.info(
            "No report has been generated yet. Use **Refresh now** in the "
            "sidebar, or run `python -m market_monitor refresh`."
        )
        return

    st.caption(
        "Updated: {}".format(format_local(report.generated_at.astimezone(tz)))
    )

    # Warnings go above the data: stale rows must never look current.
    if sections.warnings:
        render_warnings(report)

    if sections.macro_events:
        st.divider()
        macro_section.render(report)

    if sections.earnings:
        st.divider()
        earnings_section.render(report)

    if sections.data_status:
        st.divider()
        render_data_status(report, tz)

    st.divider()
    with st.expander("Text summary"):
        st.code(summarize(report), language="text")

    if sections.tradingview_widget:
        st.divider()
        tradingview_widget.render(timezone=config.app.timezone)


main()
