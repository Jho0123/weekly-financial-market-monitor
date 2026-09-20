"""EarningsAPI.com adapter.

``GET /v1/earnings?symbol=X`` returns that company's earnings history
*and* its upcoming scheduled dates, newest first. One request per watched
symbol, which is what makes this workable on the free plan: a four-name
watchlist costs four calls against a 100/day allowance, where the
date-based calendar endpoint would cost one call per day of lookahead
(30 for the default window).

The API publishes no confirmation flag, so this provider never reports a
date as ``confirmed``. A row whose reporting time is known is
``expected``; one without is ``estimated`` (spec section 13).

The key goes in the ``apikey`` query parameter -- the API accepts no
header form -- so it does end up in the request URL. Everything that
logs a URL runs it through ``utils.http.redact`` first.
"""

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
from ...utils.http import HttpFetcher, PermanentHttpError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.earningsapi.com/v1/earnings"
SOURCE_URL = "https://www.earningsapi.com/docs/earnings"
SIGNUP_URL = "https://www.earningsapi.com/"

# Free plan: 60 requests/minute, 100/day, 1000/month. One request per
# symbol, so warn before a large watchlist quietly burns the day's quota.
FREE_DAILY_QUOTA = 100

SESSION_MAP = {
    "time-pre-market": SESSION_BEFORE,
    "pre-market": SESSION_BEFORE,
    "premarket": SESSION_BEFORE,
    "bmo": SESSION_BEFORE,
    "time-after-hours": SESSION_AFTER,
    "after-hours": SESSION_AFTER,
    "afterhours": SESSION_AFTER,
    "amc": SESSION_AFTER,
    "time-during-market": SESSION_DURING,
    "during-market": SESSION_DURING,
}

# Values the API uses to say "we do not know the time".
SESSION_UNSPECIFIED = {"time-not-supplied", "not-supplied", "unspecified", "tns"}


class MissingApiKey(RuntimeError):
    pass


class InvalidApiKey(RuntimeError):
    pass


class EarningsApiProvider:
    name = "EarningsAPI"

    def __init__(
        self,
        api_key: Optional[str] = None,
        fetcher: Optional[HttpFetcher] = None,
    ):
        self.api_key = api_key or os.environ.get("EARNINGS_API_KEY") or ""
        self.fetcher = fetcher or HttpFetcher()

    # ------------------------------------------------------------ request

    def require_key(self) -> None:
        if not self.api_key:
            raise MissingApiKey(
                "EARNINGS_API_KEY is not set. Get a key at {} and add it to "
                ".env.".format(SIGNUP_URL)
            )

    def build_url(self, symbol: str) -> str:
        self.require_key()
        query = urlencode(
            {"symbol": symbol.strip().upper(), "apikey": self.api_key}
        )
        return "{}?{}".format(BASE_URL, query)

    def get_earnings(
        self, symbols: List[str], start: date, end: date
    ) -> List[EarningsEvent]:
        tickers = []
        for raw in symbols:
            symbol = raw.strip().upper()
            if symbol and symbol not in tickers:
                tickers.append(symbol)

        if not tickers:
            return []

        # Fail fast on a credential problem: it is not per-symbol, and it
        # must not be flattened into the per-symbol failure summary below.
        self.require_key()

        if len(tickers) > FREE_DAILY_QUOTA // 4:
            logger.warning(
                "Watchlist has %d symbols; this provider spends one request "
                "per symbol and the free plan allows %d per day.",
                len(tickers),
                FREE_DAILY_QUOTA,
            )

        events: List[EarningsEvent] = []
        failures: Dict[str, Exception] = {}

        for symbol in tickers:
            try:
                payload = self.fetcher.get_json(self.build_url(symbol))
            except PermanentHttpError as exc:
                if exc.status_code in (401, 403):
                    # A credential problem affects every symbol; there is
                    # nothing to be gained by trying the rest.
                    raise self._explain(exc) from exc
                if exc.status_code == 404:
                    # No coverage for this ticker is not a failure.
                    logger.info("No EarningsAPI coverage for %s", symbol)
                    continue
                failures[symbol] = exc
                continue
            except Exception as exc:  # noqa: BLE001
                failures[symbol] = exc
                continue

            events.extend(self.parse(payload, symbol, start=start))

        if failures:
            # Partial results would be written to the cache and silently
            # replace good rows for the symbols that failed, so treat any
            # real failure as a failed refresh and let the service fall
            # back to the last complete snapshot.
            raise RuntimeError(
                "EarningsAPI failed for {}: {}".format(
                    ", ".join(sorted(failures)),
                    "; ".join(str(exc) for exc in failures.values()),
                )
            )

        return events

    @staticmethod
    def _explain(exc: PermanentHttpError) -> Exception:
        if exc.status_code == 401:
            return MissingApiKey(
                "EarningsAPI rejected the request as unauthenticated. Check "
                "EARNINGS_API_KEY in .env ({}).".format(SIGNUP_URL)
            )
        return InvalidApiKey(
            "EarningsAPI rejected the key. Check EARNINGS_API_KEY in .env "
            "({}).".format(SIGNUP_URL)
        )

    # -------------------------------------------------------------- parse

    def parse(
        self, payload, symbol: str, start: Optional[date] = None
    ) -> List[EarningsEvent]:
        """Turn one symbol's response into EarningsEvent objects.

        The documented shape is a bare array; a ``{"data": [...]}``
        envelope is accepted too so a wrapper change does not break the
        Sunday run.

        The endpoint returns several years of reported results alongside
        the upcoming dates, so anything before ``start`` is dropped here:
        a forward-looking calendar never wants them, and they would
        otherwise be written to the cache on every refresh.

        Note that the window's *end* is deliberately not applied. The
        response is one request either way, and keeping the later dates
        means next week's refresh has something to fall back on if the
        API is unreachable. ``EarningsService`` applies the real window.
        """
        rows = payload
        if isinstance(payload, dict):
            for key in ("data", "results", "earnings"):
                if isinstance(payload.get(key), list):
                    rows = payload[key]
                    break
            else:
                raise ValueError(
                    "EarningsAPI returned an object with no earnings array "
                    "for {}: keys {}".format(symbol, sorted(payload))
                )

        if not isinstance(rows, list):
            raise ValueError(
                "EarningsAPI returned {} where a list was expected for "
                "{}".format(type(rows).__name__, symbol)
            )

        events = []
        for row in rows:
            event = self._row_to_event(row, symbol)
            if event is None:
                continue
            if start is not None and event.report_date < start:
                continue
            events.append(event)
        return events

    def _row_to_event(self, row, requested_symbol: str) -> Optional[EarningsEvent]:
        if not isinstance(row, dict):
            return None

        report_date = self._parse_date(row.get("date"))
        if report_date is None:
            return None

        symbol = str(row.get("symbol") or requested_symbol).strip().upper()
        session = self._parse_session(row.get("time"))

        return EarningsEvent(
            symbol=symbol,
            company_name=(row.get("name") or "").strip() or None,
            report_date=report_date,
            session=session,
            estimated_eps=self._parse_float(row.get("epsEstimate")),
            # No confirmation flag exists, so never claim one. Knowing the
            # reporting time is the only signal the company has spoken.
            status=STATUS_EXPECTED if session != SESSION_UNKNOWN else STATUS_ESTIMATED,
            source=self.name,
            source_url=SOURCE_URL,
        )

    @staticmethod
    def _parse_session(value) -> str:
        text = str(value or "").strip().lower()
        if not text or text in SESSION_UNSPECIFIED:
            return SESSION_UNKNOWN
        if text in SESSION_MAP:
            return SESSION_MAP[text]
        logger.debug("Unrecognised EarningsAPI time %r", value)
        return SESSION_UNKNOWN

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            logger.debug("Unparseable EarningsAPI date %r", value)
            return None

    @staticmethod
    def _parse_float(value) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
