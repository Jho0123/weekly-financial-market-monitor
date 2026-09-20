"""Alpha Vantage earnings calendar adapter.

``EARNINGS_CALENDAR`` returns CSV covering every listed company for a
3, 6 or 12 month horizon. The whole calendar is pulled in one request and
filtered locally, which keeps the cost at a single call per refresh --
the free tier allows 25 a day.

Alpha Vantage does not publish a confirmation flag, so this provider
never emits ``confirmed``. A row that names a session is treated as
``expected`` (the company has said when it will report) and a row without
one as ``estimated``. Uncertainty is preserved rather than flattened,
per spec section 13.
"""

import csv
import io
import logging
import os
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import urlencode

from ...models.earnings_event import (
    SESSION_AFTER,
    SESSION_BEFORE,
    SESSION_DURING,
    SESSION_UNKNOWN,
    STATUS_ESTIMATED,
    STATUS_EXPECTED,
    EarningsEvent,
)
from ...utils.http import HttpFetcher

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
SOURCE_URL = "https://www.alphavantage.co/documentation/#earnings-calendar"

SESSION_MAP = {
    "pre-market": SESSION_BEFORE,
    "premarket": SESSION_BEFORE,
    "bmo": SESSION_BEFORE,
    "post-market": SESSION_AFTER,
    "postmarket": SESSION_AFTER,
    "amc": SESSION_AFTER,
    "during-market": SESSION_DURING,
    "intraday": SESSION_DURING,
}


class AlphaVantageEarningsProvider:
    name = "Alpha Vantage"

    def __init__(
        self,
        api_key: Optional[str] = None,
        fetcher: Optional[HttpFetcher] = None,
    ):
        # The literal "demo" key serves the full calendar, so the app is
        # usable before the user registers for their own key.
        self.api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY") or "demo"
        self.fetcher = fetcher or HttpFetcher()

    @staticmethod
    def horizon_for(days: int) -> str:
        if days <= 90:
            return "3month"
        if days <= 180:
            return "6month"
        return "12month"

    def build_url(self, horizon: str) -> str:
        query = urlencode(
            {
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": self.api_key,
            }
        )
        return "{}?{}".format(BASE_URL, query)

    def fetch_text(self, horizon: str) -> str:
        return self.fetcher.get_text(self.build_url(horizon))

    def get_earnings(
        self, symbols: List[str], start: date, end: date
    ) -> List[EarningsEvent]:
        horizon = self.horizon_for((end - start).days)
        text = self.fetch_text(horizon)
        return self.parse(text, symbols)

    def parse(self, text: str, symbols: List[str]) -> List[EarningsEvent]:
        # On a rate limit or a bad key, Alpha Vantage answers with a JSON
        # note and HTTP 200. Treat that as a failure so the caller can
        # fall back to cached data instead of reporting zero earnings.
        stripped = text.lstrip()
        if stripped.startswith("{"):
            raise ValueError(
                "Alpha Vantage returned a message instead of CSV: "
                + stripped[:200].replace("\n", " ")
            )

        wanted = {symbol.strip().upper() for symbol in symbols}
        events: List[EarningsEvent] = []

        reader = csv.DictReader(io.StringIO(stripped))
        if not reader.fieldnames or "symbol" not in reader.fieldnames:
            raise ValueError(
                "Unexpected Alpha Vantage CSV header: {!r}".format(reader.fieldnames)
            )

        for row in reader:
            event = self._row_to_event(row, wanted)
            if event is not None:
                events.append(event)

        return events

    def _row_to_event(self, row: Dict[str, str], wanted) -> Optional[EarningsEvent]:
        symbol = (row.get("symbol") or "").strip().upper()
        if not symbol or symbol not in wanted:
            return None

        report_date = self._parse_date(row.get("reportDate"))
        if report_date is None:
            return None

        session_raw = (row.get("timeOfTheDay") or "").strip().lower()
        session = SESSION_MAP.get(session_raw, SESSION_UNKNOWN)

        return EarningsEvent(
            symbol=symbol,
            company_name=(row.get("name") or "").strip() or None,
            report_date=report_date,
            session=session,
            estimated_eps=self._parse_float(row.get("estimate")),
            status=STATUS_EXPECTED if session_raw else STATUS_ESTIMATED,
            source=self.name,
            source_url=SOURCE_URL,
        )

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        text = (value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            logger.debug("Unparseable earnings date %r", value)
            return None

    @staticmethod
    def _parse_float(value: Optional[str]) -> Optional[float]:
        text = (value or "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
