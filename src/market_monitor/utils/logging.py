"""Logging setup. Secrets are never passed to the logger (spec section 56)."""

import logging
import sys
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup_logging(level: str = "INFO", file: Optional[str] = None) -> None:
    global _CONFIGURED

    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(level)
        return

    root.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    if file:
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # APScheduler is chatty at INFO on every tick.
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _CONFIGURED = True
