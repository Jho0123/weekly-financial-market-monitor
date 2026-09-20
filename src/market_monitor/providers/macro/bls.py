"""Bureau of Labor Statistics release calendar.

BLS publishes its news release schedule as an official iCalendar feed,
which is far steadier than the HTML schedule pages: no selectors, no
markup churn. Covers CPI, PPI, Employment Situation, JOLTS, ECI and the
rest of the BLS programme.
"""

from typing import List

from ...models.macro_event import MacroEvent
from ...utils.dates import EASTERN
from ...utils.ics import as_datetime, parse_ics
from .base import HttpMacroProvider

ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
SCHEDULE_PAGE = "https://www.bls.gov/schedule/news_release/"


class BLSProvider(HttpMacroProvider):
    name = "BLS"
    url = ICS_URL
    source_url = SCHEDULE_PAGE

    def parse(self, text: str) -> List[MacroEvent]:
        events: List[MacroEvent] = []

        for entry in parse_ics(text):
            if not entry.summary:
                continue

            # BLS stamps its feed US-Eastern; all-day rows fall back to
            # 08:30 ET, when the agency releases most of this calendar.
            local = as_datetime(entry.start, EASTERN)
            events.append(self.make_event(entry.summary, local))

        return events
