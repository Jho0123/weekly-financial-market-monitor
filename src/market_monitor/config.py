"""YAML configuration loading and validation.

Every user-facing choice in the system lives here; no Python module is
expected to change when the user edits ``config/config.yaml``.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_CONFIG_PATH = Path("config/config.yaml")

DAYS_OF_WEEK = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    """Raised when the YAML file is missing, malformed or invalid."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppConfig(_Base):
    name: str = "Weekly Financial Market Monitor"
    timezone: str = "America/Toronto"

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "must be an IANA timezone name such as 'America/Toronto'"
            ) from exc
        return value

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


class ScheduleConfig(_Base):
    enabled: bool = True
    day_of_week: str = "sunday"
    time: str = "10:00"

    @field_validator("day_of_week")
    @classmethod
    def _valid_day(cls, value: str) -> str:
        day = value.strip().lower()
        if day not in DAYS_OF_WEEK:
            raise ValueError(
                "must be one of: " + ", ".join(sorted(DAYS_OF_WEEK))
            )
        return day

    @field_validator("time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        if not _TIME_RE.match(value.strip()):
            raise ValueError("expected 24-hour HH:MM, for example '10:00'")
        return value.strip()

    @property
    def cron_day_of_week(self) -> str:
        return DAYS_OF_WEEK[self.day_of_week]

    @property
    def hour(self) -> int:
        return int(self.time.split(":")[0])

    @property
    def minute(self) -> int:
        return int(self.time.split(":")[1])


class MacroEventRule(_Base):
    enabled: bool = True
    importance: int = Field(ge=1, le=5)
    aliases: List[str] = Field(default_factory=list)


class MacroProviderToggles(_Base):
    bls: bool = True
    bea: bool = True
    federal_reserve: bool = True
    census: bool = True

    def enabled_names(self) -> List[str]:
        return [name for name, on in self.model_dump().items() if on]


class MacroConfig(_Base):
    enabled: bool = True
    days_ahead: int = Field(default=7, ge=0)
    country: List[str] = Field(default_factory=lambda: ["United States"])
    minimum_importance: int = Field(default=3, ge=1, le=5)
    providers: MacroProviderToggles = Field(default_factory=MacroProviderToggles)
    events: Dict[str, MacroEventRule] = Field(default_factory=dict)


class EarningsConfig(_Base):
    enabled: bool = True
    provider: str = "earningsapi"
    symbols: List[str] = Field(default_factory=list)
    lookahead_days: int = Field(default=30, gt=0)

    @field_validator("symbols")
    @classmethod
    def _normalize_symbols(cls, value: List[str]) -> List[str]:
        # Ticker normalisation lives here so no other module has to
        # care about how the user typed them (spec section 25).
        seen: Dict[str, None] = {}
        for raw in value:
            symbol = raw.strip().upper()
            if symbol:
                seen[symbol] = None
        return list(seen)


class DashboardSections(_Base):
    macro_events: bool = True
    earnings: bool = True
    warnings: bool = True
    data_status: bool = True
    tradingview_widget: bool = True


class DashboardConfig(_Base):
    type: str = "streamlit"
    title: str = "Weekly Financial Market Monitor"
    sections: DashboardSections = Field(default_factory=DashboardSections)


class StorageConfig(_Base):
    type: str = "sqlite"
    path: str = "./data/market_monitor.db"


class CacheConfig(_Base):
    enabled: bool = True
    ttl_minutes: int = Field(default=60, ge=0)


class NetworkConfig(_Base):
    timeout_seconds: float = Field(default=15, gt=0)
    retries: int = Field(default=3, ge=1)
    backoff_seconds: float = Field(default=2, ge=0)


class NotificationsConfig(_Base):
    enabled: bool = False
    provider: str = "discord"
    weekly_summary: bool = True
    errors: bool = True


class OutputConfig(_Base):
    json_path: Optional[str] = "./output/current_report.json"


class LoggingConfig(_Base):
    level: str = "INFO"
    file: Optional[str] = "./logs/market_monitor.log"

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return level


class Config(_Base):
    app: AppConfig = Field(default_factory=AppConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    macro: MacroConfig = Field(default_factory=MacroConfig)
    earnings: EarningsConfig = Field(default_factory=EarningsConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("earnings")
    @classmethod
    def _symbols_present_when_enabled(cls, value: EarningsConfig) -> EarningsConfig:
        if value.enabled and not value.symbols:
            raise ValueError(
                "earnings.symbols must not be empty while earnings.enabled is true"
            )
        return value


def _format_validation_error(path: Path, error: ValidationError) -> str:
    lines = ["Invalid config: {}".format(path), ""]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append("  {}: {}".format(location, item["msg"]))
        if "input" in item and not isinstance(item["input"], (dict, list)):
            lines.append("    got: {!r}".format(item["input"]))
    return "\n".join(lines)


def load_config(path=None) -> Config:
    """Read and validate the YAML config.

    Raises ``ConfigError`` with a readable message rather than letting a
    Pydantic traceback reach the CLI.
    """
    config_path = Path(path) if path else Path(
        os.environ.get("MARKET_MONITOR_CONFIG", DEFAULT_CONFIG_PATH)
    )

    if not config_path.is_file():
        raise ConfigError("Config file not found: {}".format(config_path))

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError("Could not parse YAML in {}:\n{}".format(config_path, exc))

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(
            "Expected a mapping at the top level of {}, got {}".format(
                config_path, type(raw).__name__
            )
        )

    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(config_path, exc))
