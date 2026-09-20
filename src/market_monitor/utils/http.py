"""Shared HTTP client with timeout, retries and a polite User-Agent."""

import logging
import os
from typing import Optional

import httpx

from .retry import retry_call

logger = logging.getLogger(__name__)

RETRYABLE = (httpx.HTTPError, httpx.StreamError)

_WARNED_NO_CONTACT = False


def user_agent() -> str:
    """Identify the client and, where available, a contact address.

    Several federal sites return 403 to generic User-Agent strings and
    ask automated clients for a way to reach the operator. BLS enforces
    this: without a contact address its calendar feed refuses the
    request outright, which is worth saying plainly rather than leaving
    the user to decode a 403.
    """
    global _WARNED_NO_CONTACT

    contact = os.environ.get("CONTACT_EMAIL", "").strip()
    if contact:
        return "WeeklyMarketMonitor/1.0 ({})".format(contact)

    if not _WARNED_NO_CONTACT:
        logger.warning(
            "CONTACT_EMAIL is not set in .env. BLS rejects requests that do "
            "not identify a contact address, so the BLS calendar will fail "
            "with HTTP 403 until you set it."
        )
        _WARNED_NO_CONTACT = True

    return "WeeklyMarketMonitor/1.0 (contact unset)"


class HttpFetcher:
    """Fetches URLs as text, retrying transient failures."""

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        retries: int = 3,
        backoff_seconds: float = 2.0,
        client: Optional[httpx.Client] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={
                    "User-Agent": user_agent(),
                    "Accept": "text/html,text/calendar,text/csv,*/*",
                },
            )
        return self._client

    def get_text(self, url: str) -> str:
        def attempt() -> str:
            response = self.client.get(url)
            response.raise_for_status()
            return response.text

        return retry_call(
            attempt,
            attempts=self.retries,
            backoff_seconds=self.backoff_seconds,
            exceptions=RETRYABLE,
            description="GET {}".format(url),
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
