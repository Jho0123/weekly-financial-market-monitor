"""Normalized macro release, independent of which agency produced it."""

import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional, Union

from pydantic import BaseModel, Field

Value = Optional[Union[str, float]]


def slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_text.lower())).strip("-")


class MacroEvent(BaseModel):
    event_id: str

    canonical_name: str
    raw_name: str

    country: str = "United States"

    datetime_utc: datetime
    datetime_local: datetime

    importance: int = 0

    source: str
    source_url: Optional[str] = None

    actual: Value = None
    forecast: Value = None
    previous: Value = None

    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @staticmethod
    def build_event_id(country: str, name: str, moment: datetime) -> str:
        return "{}-{}-{}".format(
            slugify(country) or "unknown",
            slugify(name) or "event",
            moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%MZ"),
        )

    @property
    def dedupe_key(self):
        """Identity used to collapse the same release seen twice.

        Keyed on the canonical name, the UTC instant and the country, per
        spec section 42. Two sources that disagree on the time therefore
        stay as two records and raise a warning rather than one silently
        winning.
        """
        return (self.canonical_name, self.country, self.datetime_utc)

    @property
    def day_key(self):
        """Looser identity: same release, same local calendar day."""
        return (self.canonical_name, self.country, self.datetime_local.date())
