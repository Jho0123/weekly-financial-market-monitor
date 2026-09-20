"""Command line entry point.

The whole pipeline must be runnable by hand, without the scheduler
(spec section 22).
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

from .config import ConfigError, load_config
from .services.report_service import importance_label, stars, summarize
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _bootstrap(args):
    load_dotenv()
    config = load_config(args.config)
    setup_logging(config.logging.level, config.logging.file)
    return config


def cmd_validate_config(args) -> int:
    config = _bootstrap(args)

    enabled = [
        name for name, rule in config.macro.events.items() if rule.enabled
    ]
    above_minimum = [
        name
        for name, rule in config.macro.events.items()
        if rule.enabled and rule.importance >= config.macro.minimum_importance
    ]

    print("Config OK: {}".format(args.config or "config/config.yaml"))
    print("  timezone:            {}".format(config.app.timezone))
    print(
        "  schedule:            {} at {}".format(
            config.schedule.day_of_week, config.schedule.time
        )
    )
    print("  macro providers:     {}".format(
        ", ".join(config.macro.providers.enabled_names()) or "none"
    ))
    print(
        "  macro events:        {} enabled, {} at or above minimum "
        "importance {}".format(
            len(enabled), len(above_minimum), config.macro.minimum_importance
        )
    )
    print("  earnings provider:   {}".format(config.earnings.provider))
    print("  earnings symbols:    {}".format(", ".join(config.earnings.symbols)))
    print("  earnings lookahead:  {} days".format(config.earnings.lookahead_days))
    print("  storage:             {}".format(config.storage.path))
    return 0


def cmd_refresh(args) -> int:
    config = _bootstrap(args)
    from .main import refresh

    report = refresh(config, notify=not args.no_notify)
    print(summarize(report))
    if report.warnings:
        return 1
    return 0


def cmd_fetch_macro(args) -> int:
    config = _bootstrap(args)
    from .main import build_macro_service
    from .repository.sqlite_repository import SqliteRepository

    repository = SqliteRepository(config.storage.path)
    service = build_macro_service(config, repository)
    reference = datetime.now(config.app.tzinfo)
    start, end = service.window(reference)

    events, statuses, warnings = service.fetch_week(reference)

    print("Macro window: {} .. {} ({})".format(start, end, config.app.timezone))
    print()
    if not events:
        print("  (no configured events in this window)")
    for event in events:
        print(
            "  {}  {:<34} {:<5} {}  [{}]".format(
                event.datetime_local.strftime("%a %b %d %H:%M"),
                event.canonical_name,
                stars(event.importance),
                importance_label(event.importance),
                event.source,
            )
        )

    print()
    for status in statuses:
        print("  {:<18} {}".format(status.provider, status.label))
    for warning in warnings:
        print("  WARNING: {}".format(warning))

    return 0


def cmd_fetch_earnings(args) -> int:
    config = _bootstrap(args)
    from .main import build_earnings_service
    from .repository.sqlite_repository import SqliteRepository

    repository = SqliteRepository(config.storage.path)
    service = build_earnings_service(config, repository)
    reference = datetime.now(config.app.tzinfo)
    start, end = service.window(reference)

    events, status, warnings = service.fetch_upcoming(reference)

    print("Earnings window: {} .. {}".format(start, end))
    print()
    if not events:
        print("  (no monitored earnings in this window)")
    for event in events:
        print(
            "  {:<6} {}  {:>3}d  {:<14} {}".format(
                event.symbol,
                event.report_date.strftime("%b %d"),
                event.days_away(start),
                event.session_label,
                event.status_label,
            )
        )

    print()
    print("  {:<18} {}".format(status.provider, status.label))
    for warning in warnings:
        print("  WARNING: {}".format(warning))

    return 0


def cmd_summary(args) -> int:
    config = _bootstrap(args)
    from .repository.sqlite_repository import SqliteRepository

    report = SqliteRepository(config.storage.path).load_latest_report()
    if report is None:
        print("No report stored yet. Run `refresh` first.", file=sys.stderr)
        return 1
    print(summarize(report))
    return 0


def cmd_dashboard(args) -> int:
    config = _bootstrap(args)
    app_path = Path(__file__).parent / "dashboard" / "app.py"

    command = [sys.executable, "-m", "streamlit", "run", str(app_path)]
    if args.config:
        command += ["--", "--config", args.config]

    logger.info("Launching Streamlit dashboard: %s", config.dashboard.title)
    return subprocess.call(command)


def cmd_schedule(args) -> int:
    config = _bootstrap(args)
    from .scheduler import run_scheduler

    run_scheduler(config, run_now=args.run_now)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market_monitor",
        description="Weekly macro calendar and earnings watchlist.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml (default: config/config.yaml)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser(
        "refresh", help="Run the full pipeline once and store the report"
    )
    refresh_parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Skip notifications even if enabled in config",
    )
    refresh_parser.set_defaults(func=cmd_refresh)

    subparsers.add_parser(
        "fetch-macro", help="Fetch and print the macro calendar only"
    ).set_defaults(func=cmd_fetch_macro)

    subparsers.add_parser(
        "fetch-earnings", help="Fetch and print the earnings watchlist only"
    ).set_defaults(func=cmd_fetch_earnings)

    subparsers.add_parser(
        "summary", help="Print the text summary of the last stored report"
    ).set_defaults(func=cmd_summary)

    subparsers.add_parser(
        "dashboard", help="Launch the Streamlit dashboard"
    ).set_defaults(func=cmd_dashboard)

    schedule_parser = subparsers.add_parser(
        "schedule", help="Run the scheduler in the foreground"
    )
    schedule_parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run one refresh immediately, then wait for the schedule",
    )
    schedule_parser.set_defaults(func=cmd_schedule)

    subparsers.add_parser(
        "validate-config", help="Validate config.yaml and print a summary"
    ).set_defaults(func=cmd_validate_config)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
