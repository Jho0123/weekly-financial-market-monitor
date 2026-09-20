"""Bureau of Economic Analysis release calendar.

BEA offers an iCalendar subscription for its release schedule, stamped in
UTC. Covers GDP and Personal Income and Outlays (the release that carries
PCE and core PCE).

BEA titles carry the reference period, e.g.
``"Gross Domestic Product, 2nd Quarter 2026 (Second Estimate)"``. The
title is kept verbatim as ``raw_name``; the normalizer is what strips the
period down to the user's canonical label.
"""

from typing import List

from ...models.macro_event import MacroEvent
from ...utils.dates import EASTERN
from ...utils.ics import as_datetime, parse_ics
from .base import HttpMacroProvider

ICS_URL = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
SCHEDULE_PAGE = "https://www.bea.gov/news/schedule"


class BEAProvider(HttpMacroProvider):
    name = "BEA"
    url = ICS_URL
    source_url = SCHEDULE_PAGE

    def parse(self, text: str) -> List[MacroEvent]:
        events: List[MacroEvent] = []

        for entry in parse_ics(text):
            if not entry.summary:
                continue

            # BEA DTSTARTs are UTC; present them in Eastern so the local
            # date matches the release date the agency advertises.
            moment = as_datetime(entry.start, EASTERN)
            local = moment.astimezone(EASTERN)
            events.append(self.make_event(entry.summary, local))

        return events
