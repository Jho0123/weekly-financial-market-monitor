"""Macro provider contract.

A provider fetches and parses one source and returns ``MacroEvent``
objects. It never decides whether an event matters, whether it should be
displayed, or how important it is -- that belongs to the config and the
service layer (spec section 17).
"""

from datetime import date, datetime, timezone
from typing import List, Optional

try:
    from typing import Protocol
except ImportError:  # pragma: no cover - Python < 3.8
    Protocol = object  # type: ignore

from ...models.macro_event import MacroEvent
from ...utils.http import HttpFetcher


class MacroCalendarProvider(Protocol):
    name: str

    def get_events(self, start: date, end: date) -> List[MacroEvent]:
        ...


class HttpMacroProvider:
    """Base for providers that read a single public URL.

    Subclasses implement ``parse``; fetching, retries and the date filter
    are handled here so each source only owns its own parsing.
    """

    name = "unknown"
    url = ""
    source_url: Optional[str] = None

    def __init__(self, fetcher: Optional[HttpFetcher] = None):
        self.fetcher = fetcher or HttpFetcher()

    def fetch_text(self) -> str:
        return self.fetcher.get_text(self.url)

    def make_event(
        self,
        raw_name: str,
        local: datetime,
        country: str = "United States",
        source_url: Optional[str] = None,
    ) -> MacroEvent:
        """Build a MacroEvent from a source's own title and local time.

        ``canonical_name`` is set to the raw title here; the normalizer
        replaces it once the config decides what the release is called.
        """
        return MacroEvent(
            event_id=MacroEvent.build_event_id(country, raw_name, local),
            canonical_name=raw_name,
            raw_name=raw_name,
            country=country,
            datetime_utc=local.astimezone(timezone.utc),
            datetime_local=local,
            source=self.name,
            source_url=source_url or self.source_url,
        )

    def parse(self, text: str) -> List[MacroEvent]:
        raise NotImplementedError

    def get_events(self, start: date, end: date) -> List[MacroEvent]:
        events = self.parse(self.fetch_text())
        return [
            event
            for event in events
            if start <= event.datetime_local.date() <= end
        ]
