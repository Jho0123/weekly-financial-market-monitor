"""Fan out to every macro provider and survive individual failures.

One source going down must not take the report with it (spec section 37),
and a failed fetch must never erase good cached data (spec section 36).
Both rules are enforced here so no individual provider has to know about
caching or about its siblings.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, List, Optional, Sequence, Tuple

from ...models.macro_event import MacroEvent
from ...models.provider_status import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARNING,
    ProviderStatus,
)
from ...utils.dates import format_local

logger = logging.getLogger(__name__)

# Given a provider name and the window, return cached events plus the
# time they were cached. Returning ``None`` means "nothing cached".
CacheLoader = Callable[[str, date, date], Optional[Tuple[List[MacroEvent], datetime]]]

# Called with a provider name and everything it returned, after a
# successful fetch only.
CacheSaver = Callable[[str, List[MacroEvent]], None]

# Providers are asked for a window well past the reported one, and the
# whole of it is cached. Caching only the reported week would make the
# fallback useless: next week's refresh would fail over to a snapshot
# that contains nothing inside next week's window.
CACHE_LOOKAHEAD_DAYS = 120


class CompositeMacroProvider:
    def __init__(
        self,
        providers: Sequence,
        cache_loader: Optional[CacheLoader] = None,
        cache_saver: Optional[CacheSaver] = None,
        local_tz=None,
        cache_lookahead_days: int = CACHE_LOOKAHEAD_DAYS,
    ):
        self.providers = list(providers)
        self.cache_loader = cache_loader
        self.cache_saver = cache_saver
        self.local_tz = local_tz
        self.cache_lookahead_days = cache_lookahead_days

    def get_events(self, start: date, end: date):
        """Collect events from every provider.

        Returns ``(events, statuses, warnings)``. Events from a provider
        that failed are served from cache where possible, and the caller
        is told so through both the status and a warning.
        """
        events: List[MacroEvent] = []
        statuses: List[ProviderStatus] = []
        warnings: List[str] = []

        cache_end = end + timedelta(days=self.cache_lookahead_days)

        for provider in self.providers:
            attempted_at = datetime.now(timezone.utc)
            try:
                fetched = provider.get_events(start, cache_end)
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                logger.warning(
                    "provider=%s status=error error=%s", provider.name, exc
                )
                status, cached, warning = self._fall_back(
                    provider.name, start, end, attempted_at, exc
                )
                statuses.append(status)
                events.extend(cached)
                if warning:
                    warnings.append(warning)
                continue

            self._save(provider.name, fetched)

            in_window = [
                event
                for event in fetched
                if start <= event.datetime_local.date() <= end
            ]
            logger.info(
                "provider=%s status=ok events=%d", provider.name, len(in_window)
            )
            statuses.append(
                ProviderStatus(
                    provider=provider.name,
                    status=STATUS_OK,
                    last_success=attempted_at,
                    last_attempt=attempted_at,
                    event_count=len(in_window),
                )
            )
            events.extend(in_window)

        return events, statuses, warnings

    def _save(self, name: str, events: List[MacroEvent]) -> None:
        if self.cache_saver is None:
            return
        try:
            self.cache_saver(name, events)
        except Exception as exc:  # noqa: BLE001 - caching is best effort
            logger.warning("Could not cache %s snapshot: %s", name, exc)

    def _fall_back(self, name, start, end, attempted_at, error):
        """Serve the last good snapshot for a provider that just failed."""
        cached: List[MacroEvent] = []
        cached_at = None

        if self.cache_loader is not None:
            try:
                result = self.cache_loader(name, start, end)
            except Exception as cache_error:  # noqa: BLE001
                logger.warning("cache lookup for %s failed: %s", name, cache_error)
                result = None
            if result is not None:
                cached, cached_at = result

        if cached:
            stamp = cached_at
            if stamp is not None and self.local_tz is not None:
                stamp = stamp.astimezone(self.local_tz)
            when = format_local(stamp) if stamp else "an earlier run"
            warning = (
                "{} refresh failed ({}). Showing previously cached {} calendar "
                "data from {}.".format(name, error, name, when)
            )
            status = ProviderStatus(
                provider=name,
                status=STATUS_WARNING,
                last_success=cached_at,
                last_attempt=attempted_at,
                error=str(error),
                event_count=len(cached),
                from_cache=True,
            )
            return status, cached, warning

        warning = "{} refresh failed ({}) and no cached data was available.".format(
            name, error
        )
        status = ProviderStatus(
            provider=name,
            status=STATUS_ERROR,
            last_attempt=attempted_at,
            error=str(error),
            event_count=0,
        )
        return status, [], warning
