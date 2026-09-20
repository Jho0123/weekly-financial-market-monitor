"""Earnings watchlist section (spec section 29)."""

from datetime import date

import pandas as pd
import streamlit as st

from ..models.earnings_event import STATUS_CONFIRMED, STATUS_EXPECTED
from ..models.weekly_report import WeeklyReport


def render(report: WeeklyReport) -> None:
    st.subheader(
        "Upcoming earnings - next {} days".format(
            (report.earnings_end - report.earnings_start).days
        )
    )
    st.caption(
        "{} through {}".format(
            report.earnings_start.strftime("%b %d, %Y"),
            report.earnings_end.strftime("%b %d, %Y"),
        )
    )

    if not report.earnings_events:
        st.info("No monitored symbols report inside the lookahead window.")
        return

    today: date = report.earnings_start

    rows = [
        {
            "Ticker": event.symbol,
            "Company": event.company_name or "--",
            "Report Date": event.report_date.strftime("%a %b %d"),
            "Days Away": event.days_away(today),
            "Session": event.session_label,
            "Status": event.status_label,
            "Estimated EPS": (
                "--" if event.estimated_eps is None
                else "{:.2f}".format(event.estimated_eps)
            ),
            "Source": event.source,
        }
        for event in report.earnings_events
    ]

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # A future earnings date is a projection until the company confirms
    # it; saying so is part of the contract (spec section 13).
    unconfirmed = [
        event
        for event in report.earnings_events
        if event.status not in (STATUS_CONFIRMED,)
    ]
    if unconfirmed:
        expected = sum(1 for e in unconfirmed if e.status == STATUS_EXPECTED)
        st.caption(
            "{} of {} dates are not company-confirmed ({} expected, {} "
            "estimated). Treat them as projections that can move.".format(
                len(unconfirmed),
                len(report.earnings_events),
                expected,
                len(unconfirmed) - expected,
            )
        )
