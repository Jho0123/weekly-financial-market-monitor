"""Wiring and the weekly refresh pipeline.

This module orchestrates; it does no parsing of its own (spec section 48).
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from .config import Config, load_config
from .models.provider_status import STATUS_OK, ProviderStatus
from .models.weekly_report import WeeklyReport
from .notifications.base import send_safely
from .notifications.discord import DiscordNotifier
from .notifications.email import EmailNotifier
from .providers.earnings.alphavantage import AlphaVantageEarningsProvider
from .providers.macro.bea import BEAProvider
from .providers.macro.bls import BLSProvider
from .providers.macro.census import CensusProvider
from .providers.macro.composite import CompositeMacroProvider
from .providers.macro.federal_reserve import FederalReserveProvider
from .repository.sqlite_repository import SqliteRepository
from .services.earnings_service import EarningsService
from .services.macro_service import MacroService
from .services.normalizer import EventNormalizer
from .services.report_service import build_weekly_report, write_json
from .utils.http import HttpFetcher

logger = logging.getLogger(__name__)

MACRO_PROVIDER_CLASSES = {
    "bls": BLSProvider,
    "bea": BEAProvider,
    "federal_reserve": FederalReserveProvider,
    "census": CensusProvider,
}

EARNINGS_PROVIDER_CLASSES = {
    "alphavantage": AlphaVantageEarningsProvider,
}

NOTIFIER_CLASSES = {
    "discord": DiscordNotifier,
    "email": EmailNotifier,
}


def build_fetcher(config: Config) -> HttpFetcher:
    return HttpFetcher(
        timeout_seconds=config.network.timeout_seconds,
        retries=config.network.retries,
        backoff_seconds=config.network.backoff_seconds,
    )


def build_macro_service(
    config: Config,
    repository: SqliteRepository,
    fetcher: Optional[HttpFetcher] = None,
) -> MacroService:
    fetcher = fetcher or build_fetcher(config)

    providers = [
        MACRO_PROVIDER_CLASSES[name](fetcher=fetcher)
        for name in config.macro.providers.enabled_names()
        if name in MACRO_PROVIDER_CLASSES
    ]

    composite = CompositeMacroProvider(
        providers=providers,
        cache_loader=repository.load_macro_snapshot,
        cache_saver=repository.save_macro_snapshot,
        local_tz=config.app.tzinfo,
    )

    return MacroService(
        provider=composite,
        normalizer=EventNormalizer(config.macro.events),
        config=config,
    )


def build_earnings_service(
    config: Config,
    repository: SqliteRepository,
    fetcher: Optional[HttpFetcher] = None,
) -> EarningsService:
    fetcher = fetcher or build_fetcher(config)

    provider_class = EARNINGS_PROVIDER_CLASSES.get(config.earnings.provider)
    if provider_class is None:
        raise ValueError(
            "Unknown earnings provider {!r}. Available: {}".format(
                config.earnings.provider, ", ".join(sorted(EARNINGS_PROVIDER_CLASSES))
            )
        )

    return EarningsService(
        provider=provider_class(fetcher=fetcher),
        config=config,
        cache_loader=repository.load_earnings_snapshot,
        cache_saver=repository.save_earnings_snapshot,
        local_tz=config.app.tzinfo,
    )


def build_notifier(config: Config):
    notifier_class = NOTIFIER_CLASSES.get(config.notifications.provider)
    if notifier_class is None:
        logger.error(
            "Unknown notification provider %r", config.notifications.provider
        )
        return None
    return notifier_class()


def _worst(statuses: List[ProviderStatus]) -> str:
    if not statuses:
        return "skipped"
    for level in ("error", "warning"):
        if any(status.status == level for status in statuses):
            return level
    return STATUS_OK


def refresh(config: Optional[Config] = None, notify: bool = True) -> WeeklyReport:
    """Run the full weekly pipeline and persist the result."""
    config = config or load_config()

    repository = SqliteRepository(config.storage.path)
    started_at = datetime.now(timezone.utc)
    run_id = repository.start_run(started_at)
    logger.info("refresh_started")

    reference = datetime.now(config.app.tzinfo)

    macro_events = []
    macro_statuses: List[ProviderStatus] = []
    warnings: List[str] = []

    if config.macro.enabled:
        macro_service = build_macro_service(config, repository)
        macro_start, macro_end = macro_service.window(reference)
        macro_events, macro_statuses, macro_warnings = macro_service.fetch_week(
            reference
        )
        warnings.extend(macro_warnings)
    else:
        from .utils.dates import week_window

        macro_start, macro_end = week_window(reference, config.macro.days_ahead)

    earnings_service = build_earnings_service(config, repository)
    earnings_start, earnings_end = earnings_service.window(reference)
    earnings_events, earnings_status, earnings_warnings = (
        earnings_service.fetch_upcoming(reference)
    )
    warnings.extend(earnings_warnings)

    statuses = list(macro_statuses) + [earnings_status]

    report = build_weekly_report(
        generated_at=reference,
        macro_start=macro_start,
        macro_end=macro_end,
        earnings_start=earnings_start,
        earnings_end=earnings_end,
        macro_events=macro_events,
        earnings_events=earnings_events,
        warnings=warnings,
        provider_statuses=statuses,
    )

    repository.save_report(report)
    write_json(report, config.output.json_path)

    repository.finish_run(
        run_id=run_id,
        completed_at=datetime.now(timezone.utc),
        macro_status=_worst(macro_statuses),
        earnings_status=earnings_status.status,
        macro_event_count=len(macro_events),
        earnings_event_count=len(earnings_events),
        error_message="; ".join(warnings) if warnings else None,
    )

    if notify and config.notifications.enabled:
        notifier = build_notifier(config)
        if notifier is not None:
            send_safely(notifier, report)

    logger.info("refresh_completed")
    return report
