"""Discord webhook notifier. The URL comes from .env, never from YAML."""

import logging
import os

import httpx

from ..models.weekly_report import WeeklyReport
from ..services.report_service import summarize

logger = logging.getLogger(__name__)

# Discord rejects messages over 2000 characters.
MAX_CONTENT = 1900


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url=None, timeout_seconds: float = 15.0):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.timeout_seconds = timeout_seconds

    def send(self, report: WeeklyReport) -> None:
        if not self.webhook_url:
            raise RuntimeError(
                "DISCORD_WEBHOOK_URL is not set; add it to .env to enable "
                "Discord notifications"
            )

        body = summarize(report)
        if len(body) > MAX_CONTENT:
            body = body[: MAX_CONTENT - 3] + "..."

        response = httpx.post(
            self.webhook_url,
            json={"content": "```\n{}\n```".format(body)},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
