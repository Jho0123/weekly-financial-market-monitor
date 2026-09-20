"""FOMC meeting calendar from federalreserve.gov.

The Fed does not publish a release feed, so this parses the official FOMC
calendar page. Each meeting row gives a month cell and a day-range cell;
the rate decision and statement land at 14:00 ET on the *final* day of
the meeting, and minutes are released at 14:00 ET on the date printed in
the minutes cell.

Unlike the statistical agencies, the Fed page states meetings rather than
named releases, so this provider synthesises three release titles from
each meeting. Those titles are still just ``raw_name`` values: the config
decides whether the user cares about them.
"""

import logging
import re
from datetime import date, datetime, time
from typing import List, Optional

from bs4 import BeautifulSoup

from ...models.macro_event import MacroEvent
from ...utils.dates import EASTERN
from .base import HttpMacroProvider

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

# Statements and minutes are both released at 2:00 p.m. Eastern.
DECISION_TIME = time(14, 0)

RATE_DECISION = "Federal Reserve Interest Rate Decision"
STATEMENT = "FOMC Statement"
MINUTES = "FOMC Minutes"

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_YEAR_RE = re.compile(r"(20\d{2})\s+FOMC")
_DAYS_RE = re.compile(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?")
_RELEASED_RE = re.compile(
    r"\(\s*Released\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s*\)", re.IGNORECASE
)


def _parse_months(cell: str) -> List[int]:
    """``"January"`` -> ``[1]``; ``"Apr/May"`` -> ``[4, 5]``."""
    months = []
    for part in cell.split("/"):
        key = part.strip().lower().rstrip(".")
        if key in MONTHS:
            months.append(MONTHS[key])
    return months


class FederalReserveProvider(HttpMacroProvider):
    name = "Federal Reserve"
    url = CALENDAR_URL
    source_url = CALENDAR_URL

    def parse(self, text: str) -> List[MacroEvent]:
        soup = BeautifulSoup(text, "html.parser")
        events: List[MacroEvent] = []

        for panel in soup.find_all("div", class_="panel"):
            heading = panel.find(["h4", "h5"])
            if heading is None:
                continue

            year_match = _YEAR_RE.search(heading.get_text(" ", strip=True))
            if year_match is None:
                # e.g. the "FOMC Search" panel.
                continue
            year = int(year_match.group(1))

            for row in panel.find_all("div", class_="fomc-meeting"):
                events.extend(self._parse_meeting(row, year))

        return events

    def _parse_meeting(self, row, year: int) -> List[MacroEvent]:
        month_cell = row.find("div", class_="fomc-meeting__month")
        date_cell = row.find("div", class_="fomc-meeting__date")
        if month_cell is None or date_cell is None:
            return []

        months = _parse_months(month_cell.get_text(" ", strip=True))
        if not months:
            return []

        date_text = date_cell.get_text(" ", strip=True)
        days_match = _DAYS_RE.search(date_text)
        if days_match is None:
            return []

        first_day = int(days_match.group(1))
        last_day = int(days_match.group(2)) if days_match.group(2) else first_day

        # A meeting listed as "Apr/May" + "30-1" ends on May 1; a meeting
        # inside one month ends in that month. The decision follows the
        # final day either way.
        last_month = months[-1]
        last_year = year
        if len(months) > 1 and months[-1] < months[0]:
            # A Dec/Jan meeting would roll into the following year.
            last_year = year + 1

        decision_day = self._safe_date(last_year, last_month, last_day)
        if decision_day is None:
            return []

        decision_at = datetime.combine(decision_day, DECISION_TIME, tzinfo=EASTERN)

        events = [
            self.make_event(RATE_DECISION, decision_at),
            self.make_event(STATEMENT, decision_at),
        ]

        minutes_at = self._parse_minutes_release(row)
        if minutes_at is not None:
            events.append(self.make_event(MINUTES, minutes_at))

        return events

    def _parse_minutes_release(self, row) -> Optional[datetime]:
        cell = row.find("div", class_="fomc-meeting__minutes")
        if cell is None:
            return None

        match = _RELEASED_RE.search(cell.get_text(" ", strip=True))
        if match is None:
            # Minutes for a meeting that has not happened yet.
            return None

        month = MONTHS.get(match.group(1).strip().lower())
        if month is None:
            return None

        released = self._safe_date(int(match.group(3)), month, int(match.group(2)))
        if released is None:
            return None
        return datetime.combine(released, DECISION_TIME, tzinfo=EASTERN)

    @staticmethod
    def _safe_date(year: int, month: int, day: int) -> Optional[date]:
        try:
            return date(year, month, day)
        except ValueError:
            logger.debug("Skipping impossible FOMC date %s-%s-%s", year, month, day)
            return None
