"""Turn raw provider events into the filtered macro calendar.

The pipeline follows spec section 18: normalize names, match YAML rules,
drop disabled events, attach the user's importance, apply the minimum,
filter the window, deduplicate, sort.
"""

import logging
from datetime import date
from typing import Dict, List, Sequence, Tuple

from ..config import Config
from ..models.macro_event import MacroEvent
from ..providers.macro.composite import CompositeMacroProvider
from ..utils.dates import format_local, week_window
from .normalizer import EventNormalizer

logger = logging.getLogger(__name__)

# Which agency owns which release, used to settle disagreements
# (spec section 65). The official publisher wins the ordering; a
# conflicting date from another source is still kept and warned about.
SOURCE_PRIORITY = {
    "BLS": 0,
    "BEA": 0,
    "Federal Reserve": 0,
    "Census": 0,
}
DEFAULT_PRIORITY = 10


class MacroService:
    def __init__(
        self,
        provider: CompositeMacroProvider,
        normalizer: EventNormalizer,
        config: Config,
    ):
        self.provider = provider
        self.normalizer = normalizer
        self.config = config

    def window(self, reference) -> Tuple[date, date]:
        return week_window(reference, self.config.macro.days_ahead)

    def fetch_week(self, reference):
        """Return ``(events, statuses, warnings)`` for the report window."""
        start, end = self.window(reference)

        raw_events, statuses, warnings = self.provider.get_events(start, end)

        matched = self.apply_rules(raw_events)
        in_window = [
            event for event in matched if start <= event.datetime_local.date() <= end
        ]

        deduped, conflicts = self.deduplicate(in_window)
        warnings = list(warnings) + conflicts

        deduped.sort(key=lambda event: (event.datetime_local, event.canonical_name))
        return deduped, statuses, warnings

    def apply_rules(self, events: Sequence[MacroEvent]) -> List[MacroEvent]:
        """Keep only configured, enabled, important-enough events."""
        macro = self.config.macro
        countries = {country.strip().lower() for country in macro.country}
        results: List[MacroEvent] = []

        for event in events:
            if countries and event.country.strip().lower() not in countries:
                continue

            canonical = self.normalizer.normalize(event.raw_name)
            if canonical is None:
                logger.debug("Ignoring unconfigured release %r", event.raw_name)
                continue

            rule = macro.events.get(canonical)
            if rule is None or not rule.enabled:
                continue

            if rule.importance < macro.minimum_importance:
                continue

            # Rebuild rather than mutate: the event id encodes the name.
            results.append(
                event.model_copy(
                    update={
                        "canonical_name": canonical,
                        "importance": rule.importance,
                        "event_id": MacroEvent.build_event_id(
                            event.country, canonical, event.datetime_local
                        ),
                    }
                )
            )

        return results

    def deduplicate(self, events: Sequence[MacroEvent]):
        """Collapse identical events; warn instead of silently picking.

        Two records for the same release at the same instant are one
        event, and the agency that owns the release is kept. Two records
        for the same release on the same local day at *different* times
        are a genuine disagreement: both are kept and a warning is
        raised (spec sections 42 and 65).
        """
        by_key: Dict[tuple, MacroEvent] = {}

        for event in events:
            existing = by_key.get(event.dedupe_key)
            if existing is None or self._rank(event) < self._rank(existing):
                by_key[event.dedupe_key] = event

        kept = list(by_key.values())
        return kept, self._conflict_warnings(kept)

    @staticmethod
    def _rank(event: MacroEvent) -> int:
        return SOURCE_PRIORITY.get(event.source, DEFAULT_PRIORITY)

    @staticmethod
    def _conflict_warnings(events: Sequence[MacroEvent]) -> List[str]:
        by_day: Dict[tuple, List[MacroEvent]] = {}
        for event in events:
            by_day.setdefault(event.day_key, []).append(event)

        warnings: List[str] = []
        for group in by_day.values():
            if len(group) < 2:
                continue
            group = sorted(group, key=lambda event: event.datetime_local)
            detail = "; ".join(
                "{}: {}".format(event.source, format_local(event.datetime_local))
                for event in group
            )
            warnings.append(
                "{} schedule mismatch detected -- sources disagree on the "
                "time. {}".format(group[0].canonical_name, detail)
            )
        return warnings

    def unmatched_names(self, events: Sequence[MacroEvent]) -> List[str]:
        """Release titles no configured event claimed. Useful for tuning aliases."""
        names = {
            event.raw_name
            for event in events
            if self.normalizer.normalize(event.raw_name) is None
        }
        return sorted(names)
