"""APScheduler wrapper. Every value comes from YAML (spec section 21)."""

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import Config
from .main import refresh

logger = logging.getLogger(__name__)


def _run_refresh(config: Config) -> None:
    try:
        refresh(config)
    except Exception as exc:  # noqa: BLE001 - a bad week must not kill the loop
        logger.exception("Scheduled refresh failed: %s", exc)


def build_scheduler(config: Config) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=config.app.tzinfo)
    scheduler.add_job(
        _run_refresh,
        trigger="cron",
        args=[config],
        day_of_week=config.schedule.cron_day_of_week,
        hour=config.schedule.hour,
        minute=config.schedule.minute,
        timezone=config.app.tzinfo,
        id="weekly_refresh",
        # A missed run (laptop asleep, machine rebooting) should still
        # fire rather than be silently dropped.
        misfire_grace_time=60 * 60,
        coalesce=True,
    )
    return scheduler


def run_scheduler(config: Config, run_now: bool = False) -> None:
    if not config.schedule.enabled:
        logger.warning(
            "schedule.enabled is false in config; nothing will run. "
            "Set it to true or use `refresh` for a one-off run."
        )
        return

    if run_now:
        _run_refresh(config)

    scheduler = build_scheduler(config)
    logger.info(
        "Scheduler started: %s at %s %s",
        config.schedule.day_of_week,
        config.schedule.time,
        config.app.timezone,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
