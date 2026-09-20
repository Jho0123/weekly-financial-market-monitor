"""U.S. Census Bureau economic indicator calendar.

The list view of the economic indicator calendar is a plain table whose
date cell carries a ``sorttable_customkey="YYYYMMDDHHMM"`` attribute.
Reading that attribute is steadier than parsing the human-readable date
and time, so it is preferred and the visible text is only a fallback.

Covers Retail Sales, Durable Goods, New Residential Construction and the
rest of the Census indicator programme.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from ...models.macro_event import MacroEvent
from ...utils.dates import EASTERN
from .base import HttpMacroProvider

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"
INDICATORS_PAGE = "https://www.census.gov/economic-indicators/"

_KEY_RE = re.compile(r"^\s*(\d{12})\s*$")


class CensusProvider(HttpMacroProvider):
    name = "Census"
    url = CALENDAR_URL
    source_url = INDICATORS_PAGE

    def parse(self, text: str) -> List[MacroEvent]:
        soup = BeautifulSoup(text, "html.parser")
        events: List[MacroEvent] = []

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            name = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip()
            if not name:
                continue

            local = self._row_datetime(cells)
            if local is None:
                continue

            events.append(self.make_event(name, local))

        return events

    def _row_datetime(self, cells) -> Optional[datetime]:
        for cell in cells[1:]:
            key = cell.get("sorttable_customkey")
            if not key:
                continue
            match = _KEY_RE.match(str(key))
            if not match:
                continue
            try:
                parsed = datetime.strptime(match.group(1), "%Y%m%d%H%M")
            except ValueError:
                logger.debug("Unparseable Census sort key %r", key)
                continue
            # Census publishes its calendar in Eastern time.
            return parsed.replace(tzinfo=EASTERN)
        return None
