"""Fetch and filter the earnings watchlist (spec sections 23-25)."""

import logging
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..config import Config
from ..models.earnings_event import EarningsEvent
from ..models.provider_status import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_SKIPPED,
    STATUS_WARNING,
    ProviderStatus,
)
from ..utils.dates import earnings_window, format_local

logger = logging.getLogger(__name__)

CacheLoader = Callable[
    [date, date], Optional[Tuple[List[EarningsEvent], datetime]]
]
CacheSaver = Callable[[List[EarningsEvent]], None]


class EarningsService:
    def __init__(
        self,
        provider,
        config: Config,
        cache_loader: Optional[CacheLoader] = None,
        cache_saver: Optional[CacheSaver] = None,
        local_tz=None,
    ):
        self.provider = provider
        self.config = config
        self.cache_loader = cache_loader
        self.cache_saver = cache_saver
        self.local_tz = local_tz

    def window(self, reference) -> Tuple[date, date]:
        return earnings_window(reference, self.config.earnings.lookahead_days)

    def fetch_upcoming(self, reference):
        """Return ``(events, status, warnings)`` for the lookahead window."""
        start, end = self.window(reference)
        symbols = self.config.earnings.symbols
        attempted_at = datetime.now(timezone.utc)

        if not self.config.earnings.enabled:
            return (
                [],
                ProviderStatus(
                    provider=self.provider.name,
                    status=STATUS_SKIPPED,
                    last_attempt=attempted_at,
                ),
                [],
            )

        try:
            raw = self.provider.get_earnings(symbols=symbols, start=start, end=end)
        except Exception as exc:  # noqa: BLE001 - must not fail the report
            logger.warning(
                "provider=EARNINGS status=error error=%s", exc
            )
            return self._fall_back(start, end, attempted_at, exc)

        # Cache everything the provider returned, not just this week's
        # slice: next week's window has moved, and a snapshot trimmed to
        # today's window would have nothing to offer it.
        self._save(raw)

        events = self.filter_events(raw, start, end, symbols)
        logger.info("provider=EARNINGS status=ok events=%d", len(events))

        return (
            events,
            ProviderStatus(
                provider=self.provider.name,
                status=STATUS_OK,
                last_success=attempted_at,
                last_attempt=attempted_at,
                event_count=len(events),
            ),
            [],
        )

    def filter_events(
        self,
        events: Sequence[EarningsEvent],
        start: date,
        end: date,
        symbols: Sequence[str],
    ) -> List[EarningsEvent]:
        """Keep watchlist symbols inside the window; dedupe; sort.

        The provider is asked to filter too, but this is the boundary
        that actually guarantees it -- a provider that ignores the
        arguments cannot leak extra symbols or dates into the report.
        """
        wanted = {symbol.strip().upper() for symbol in symbols}
        by_key: Dict[tuple, EarningsEvent] = {}

        for event in events:
            symbol = event.symbol.strip().upper()
            if symbol not in wanted:
                continue
            if not (start <= event.report_date <= end):
                continue

            normalized = (
                event
                if event.symbol == symbol
                else event.model_copy(update={"symbol": symbol})
            )
            by_key.setdefault(normalized.dedupe_key, normalized)

        return sorted(
            by_key.values(), key=lambda event: (event.report_date, event.symbol)
        )

    def _save(self, events: Sequence[EarningsEvent]) -> None:
        if self.cache_saver is None:
            return
        try:
            self.cache_saver(list(events))
        except Exception as exc:  # noqa: BLE001 - caching is best effort
            logger.warning("Could not cache earnings snapshot: %s", exc)

    def _fall_back(self, start, end, attempted_at, error):
        cached: List[EarningsEvent] = []
        cached_at = None

        if self.cache_loader is not None:
            try:
                result = self.cache_loader(start, end)
            except Exception as cache_error:  # noqa: BLE001
                logger.warning("earnings cache lookup failed: %s", cache_error)
                result = None
            if result is not None:
                rows, cached_at = result
                # The snapshot spans the provider's whole horizon and may
                # predate a watchlist edit, so it gets the same filter a
                # live fetch would.
                cached = self.filter_events(
                    rows, start, end, self.config.earnings.symbols
                )

        if cached:
            stamp = cached_at
            if stamp is not None and self.local_tz is not None:
                stamp = stamp.astimezone(self.local_tz)
            when = format_local(stamp) if stamp else "an earlier run"
            warning = (
                "Earnings refresh failed ({}). Showing previously cached "
                "earnings data from {}.".format(error, when)
            )
            status = ProviderStatus(
                provider=self.provider.name,
                status=STATUS_WARNING,
                last_success=cached_at,
                last_attempt=attempted_at,
                error=str(error),
                event_count=len(cached),
                from_cache=True,
            )
            return cached, status, [warning]

        warning = (
            "Earnings refresh failed ({}) and no cached data was "
            "available.".format(error)
        )
        status = ProviderStatus(
            provider=self.provider.name,
            status=STATUS_ERROR,
            last_attempt=attempted_at,
            error=str(error),
        )
        return [], status, [warning]
