"""Macro calendar section.

Reads only the normalized WeeklyReport. It has no idea which agency any
row came from beyond the ``source`` field it prints (spec section 4).
"""

from typing import List

import pandas as pd
import streamlit as st

from ..models.macro_event import MacroEvent
from ..models.weekly_report import WeeklyReport
from ..services.report_service import importance_label, stars


def _rows(events: List[MacroEvent]):
    for event in events:
        yield {
            "Date": event.datetime_local.strftime("%a %b %d"),
            "Time": event.datetime_local.strftime("%H:%M %Z"),
            "Event": event.canonical_name,
            "Importance": stars(event.importance),
            "Level": importance_label(event.importance),
            "Forecast": event.forecast if event.forecast is not None else "--",
            "Previous": event.previous if event.previous is not None else "--",
            "Actual": event.actual if event.actual is not None else "--",
            "Country": event.country,
            "Source": event.source,
        }


def render(report: WeeklyReport) -> None:
    st.subheader("Important events this week")
    st.caption(
        "{} through {}".format(
            report.macro_start.strftime("%A %b %d, %Y"),
            report.macro_end.strftime("%A %b %d, %Y"),
        )
    )

    if not report.macro_events:
        st.info(
            "No configured macro events fall in this window. That is a normal "
            "result for a quiet week -- check Data status below to confirm the "
            "sources were actually reached."
        )
        return

    levels = sorted({event.importance for event in report.macro_events}, reverse=True)
    chosen = st.multiselect(
        "Importance",
        options=levels,
        default=levels,
        format_func=lambda value: "{} {}".format(stars(value), importance_label(value)),
    )

    events = [event for event in report.macro_events if event.importance in chosen]
    if not events:
        st.warning("No events match the selected importance levels.")
        return

    # Day-by-day view first: this is the shape the weekly planner is read in.
    for day in sorted({event.datetime_local.date() for event in events}):
        st.markdown("**{}**".format(day.strftime("%A %b %d")))
        for event in [e for e in events if e.datetime_local.date() == day]:
            columns = st.columns([1.2, 4, 2])
            columns[0].write(event.datetime_local.strftime("%H:%M"))
            columns[1].write(event.canonical_name)
            columns[2].write(stars(event.importance))
            if event.forecast is not None or event.previous is not None:
                columns[1].caption(
                    "Forecast: {} | Previous: {}".format(
                        event.forecast if event.forecast is not None else "--",
                        event.previous if event.previous is not None else "--",
                    )
                )

    with st.expander("Table view"):
        st.dataframe(
            pd.DataFrame(list(_rows(events))),
            width="stretch",
            hide_index=True,
        )
