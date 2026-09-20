"""Map an agency's release title onto the user's canonical event name.

Aliases come from YAML rather than a hardcoded table (spec section 15),
so adding a source or renaming an event needs no code change.

Matching is exact, against progressively simplified forms of the title:

    "Gross Domestic Product, 2nd Quarter 2026 (Second Estimate)"
      -> "gross domestic product 2nd quarter 2026 second estimate"   (miss)
      -> "gross domestic product 2nd quarter 2026"                   (miss)
      -> "gross domestic product"                                    (hit -> GDP)

Substring matching is deliberately avoided. BEA publishes both
"Gross Domestic Product" and "Gross Domestic Product by State and
Personal Income by State"; a substring rule would quietly file the
regional release as the headline GDP print.
"""

import re
import unicodedata
from typing import Dict, Iterable, List, Optional

_PAREN_RE = re.compile(r"\([^()]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str) -> str:
    """Casefold, strip accents and collapse punctuation to single spaces.

    Applied identically to aliases and to raw titles, so
    ``"Durable Goods--Manufacturers' Shipments"`` and the same string
    typed with different punctuation still compare equal.
    """
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return _NON_ALNUM_RE.sub(" ", ascii_text.lower()).strip()


def _strip_parentheticals(text: str) -> str:
    previous = None
    current = text
    while previous != current:
        previous = current
        current = _PAREN_RE.sub(" ", current)
    return current


def candidate_forms(raw_name: str) -> List[str]:
    """Simplified forms of a title, most specific first."""
    without_parens = _strip_parentheticals(raw_name)
    before_comma = raw_name.split(",", 1)[0]
    before_comma_no_parens = _strip_parentheticals(before_comma)

    forms = [
        raw_name,
        without_parens,
        before_comma,
        before_comma_no_parens,
    ]

    seen = []
    for form in forms:
        normalized = normalize_text(form)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


class EventNormalizer:
    """Resolves raw release titles to canonical names from the config."""

    def __init__(self, events: Optional[Dict[str, object]] = None):
        self._aliases: Dict[str, str] = {}
        if events:
            self.load(events)

    def load(self, events: Dict[str, object]) -> None:
        """Index every canonical name and its aliases.

        The canonical name is always its own alias, so an agency that
        already uses the user's label matches without extra config.
        """
        for canonical, rule in events.items():
            self._register(canonical, canonical)
            for alias in self._rule_aliases(rule):
                self._register(alias, canonical)

    @staticmethod
    def _rule_aliases(rule) -> Iterable[str]:
        aliases = getattr(rule, "aliases", None)
        if aliases is None and isinstance(rule, dict):
            aliases = rule.get("aliases")
        return aliases or []

    def _register(self, alias: str, canonical: str) -> None:
        key = normalize_text(alias)
        if not key:
            return
        # First registration wins, so a canonical name is never
        # shadowed by another event's alias.
        self._aliases.setdefault(key, canonical)

    def normalize(self, raw_name: str) -> Optional[str]:
        """Canonical name for ``raw_name``, or ``None`` if unconfigured."""
        for form in candidate_forms(raw_name):
            canonical = self._aliases.get(form)
            if canonical is not None:
                return canonical
        return None

    @property
    def alias_count(self) -> int:
        return len(self._aliases)
