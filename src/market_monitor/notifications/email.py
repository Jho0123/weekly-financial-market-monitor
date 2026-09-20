"""SMTP notifier. Credentials come from .env, never from YAML."""

import logging
import os
import smtplib
from email.message import EmailMessage

from ..models.weekly_report import WeeklyReport
from ..services.report_service import summarize

logger = logging.getLogger(__name__)


class EmailNotifier:
    name = "email"

    def __init__(self):
        self.host = os.environ.get("SMTP_HOST", "")
        self.port = int(os.environ.get("SMTP_PORT") or 587)
        self.username = os.environ.get("SMTP_USERNAME", "")
        self.password = os.environ.get("SMTP_PASSWORD", "")
        self.sender = os.environ.get("EMAIL_FROM", "")
        self.recipients = [
            address.strip()
            for address in os.environ.get("EMAIL_TO", "").split(",")
            if address.strip()
        ]

    def send(self, report: WeeklyReport) -> None:
        missing = [
            name
            for name, value in (
                ("SMTP_HOST", self.host),
                ("EMAIL_FROM", self.sender),
                ("EMAIL_TO", self.recipients),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Email notifications need these .env values: "
                + ", ".join(missing)
            )

        message = EmailMessage()
        message["Subject"] = "Weekly Market Monitor: {} - {}".format(
            report.macro_start.strftime("%b %d"),
            report.macro_end.strftime("%b %d"),
        )
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(summarize(report))

        with smtplib.SMTP(self.host, self.port, timeout=30) as server:
            server.starttls()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(message)
