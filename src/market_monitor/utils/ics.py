"""A small iCalendar reader for the BLS and BEA release feeds.

Only the subset RFC 5545 feeds actually use here is supported: folded
lines, VEVENT blocks, and DTSTART in its UTC, floating-with-TZID and
DATE forms. That is cheaper and more predictable than pulling in a full
iCalendar dependency for four fields.
"""

import re
from datetime import date, datetime, time, timezone
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Feeds do not always use IANA identifiers. BLS publishes
# "TZID=US-Eastern", which zoneinfo cannot resolve.
TZID_ALIASES = {
    "us-eastern": "America/New_York",
    "us eastern": "America/New_York",
    "eastern standard time": "America/New_York",
    "eastern daylight time": "America/New_York",
    "us/eastern": "America/New_York",
    "us-central": "America/Chicago",
    "us/central": "America/Chicago",
    "us-mountain": "America/Denver",
    "us/mountain": "America/Denver",
    "us-pacific": "America/Los_Angeles",
    "us/pacific": "America/Los_Angeles",
}

_UNFOLD_RE = re.compile(r"\r?\n[ \t]")


class IcsEvent:
    """One VEVENT, reduced to the fields a release calendar needs."""

    def __init__(self, summary: str, start, uid: Optional[str], all_day: bool):
        self.summary = summary
        self.start = start
        self.uid = uid
        self.all_day = all_day

    def __repr__(self) -> str:
        return "IcsEvent({!r}, {!r})".format(self.summary, self.start)


def resolve_tzid(tzid: str) -> ZoneInfo:
    key = tzid.strip().strip('"').lower()
    name = TZID_ALIASES.get(key, tzid.strip().strip('"'))
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # An unknown zone must not lose the whole feed; Eastern is the
        # right guess for every U.S. federal release calendar.
        return ZoneInfo("America/New_York")


def unescape_text(value: str) -> str:
    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            nxt = value[index + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            elif nxt in (",", ";", "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _split_property(line: str):
    """``NAME;PARAM=VAL:value`` -> ``(NAME, {PARAM: VAL}, value)``."""
    colon = -1
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ":" and not in_quotes:
            colon = index
            break
    if colon < 0:
        return None

    head, value = line[:colon], line[colon + 1 :]
    parts = head.split(";")
    name = parts[0].strip().upper()

    params: Dict[str, str] = {}
    for param in parts[1:]:
        if "=" in param:
            key, val = param.split("=", 1)
            params[key.strip().upper()] = val.strip()
    return name, params, value


def parse_datetime(value: str, params: Dict[str, str]):
    """Return an aware ``datetime``, or a ``date`` for all-day entries."""
    raw = value.strip()

    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", raw):
        parsed = datetime.strptime(raw[:8], "%Y%m%d")
        return date(parsed.year, parsed.month, parsed.day)

    match = re.fullmatch(r"(\d{8})T(\d{6})(Z?)", raw)
    if not match:
        raise ValueError("unrecognised DTSTART value: {!r}".format(value))

    day = datetime.strptime(match.group(1), "%Y%m%d").date()
    clock = datetime.strptime(match.group(2), "%H%M%S").time()

    if match.group(3) == "Z":
        return datetime.combine(day, clock, tzinfo=timezone.utc)

    tzid = params.get("TZID")
    tz = resolve_tzid(tzid) if tzid else ZoneInfo("America/New_York")
    return datetime.combine(day, clock, tzinfo=tz)


def parse_ics(text: str) -> List[IcsEvent]:
    """Parse VEVENTs. Individually malformed events are skipped."""
    unfolded = _UNFOLD_RE.sub("", text)

    events: List[IcsEvent] = []
    current: Optional[Dict[str, object]] = None

    for line in unfolded.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.upper() == "BEGIN:VEVENT":
            current = {}
            continue

        if stripped.upper() == "END:VEVENT":
            if current is not None:
                summary = current.get("summary")
                start = current.get("start")
                if summary and start is not None:
                    events.append(
                        IcsEvent(
                            summary=str(summary),
                            start=start,
                            uid=current.get("uid"),
                            all_day=not isinstance(start, datetime),
                        )
                    )
            current = None
            continue

        # Properties outside a VEVENT (X-WR-CALNAME, a calendar-level
        # SUMMARY, VTIMEZONE internals) are deliberately ignored.
        if current is None:
            continue

        parsed = _split_property(stripped)
        if parsed is None:
            continue
        name, params, value = parsed

        if name == "SUMMARY":
            current["summary"] = unescape_text(value).strip()
        elif name == "UID":
            current["uid"] = value.strip()
        elif name == "DTSTART":
            try:
                current["start"] = parse_datetime(value, params)
            except ValueError:
                current["start"] = None

    return events


def as_datetime(value, tz: ZoneInfo, default_time: time = time(8, 30)) -> datetime:
    """Coerce a parsed DTSTART into an aware datetime.

    All-day entries get ``default_time`` in ``tz`` so they can still be
    ordered against timed releases.
    """
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, default_time, tzinfo=tz)
