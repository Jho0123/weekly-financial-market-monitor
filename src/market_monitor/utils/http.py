"""Shared HTTP client with timeout, retries and a polite User-Agent."""

import logging
import os
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .retry import retry_call

logger = logging.getLogger(__name__)

RETRYABLE = (httpx.HTTPError, httpx.StreamError)

# 4xx statuses that a retry could plausibly clear. Everything else in the
# 4xx range is a decision about the request itself -- a bad key, a plan
# that lacks the data, a wrong path -- and retrying it three times with
# backoff only delays the fallback and fills the log with noise.
RETRYABLE_CLIENT_STATUSES = frozenset({408, 425, 429})


class PermanentHttpError(Exception):
    """A client error no retry will fix. Deliberately not in RETRYABLE."""

    def __init__(self, message: str, status_code: int, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body

# Query parameters whose values must never reach a log file or a warning
# shown on the dashboard (spec section 56).
SECRET_PARAMS = frozenset({"apikey", "api_key", "key", "token", "access_token"})

_WARNED_NO_CONTACT = False


def redact(url: str) -> str:
    """Strip secret query parameters from a URL before it is logged.

    Provider failures surface as warnings on the dashboard and in the
    log file, and several data APIs take the key as a query parameter,
    so an un-redacted URL would publish it on the first timeout.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url

    cleaned = [
        (name, "REDACTED" if name.lower() in SECRET_PARAMS else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(cleaned)))


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

    @staticmethod
    def _check(response, url: str) -> None:
        """Raise a permanent error for client mistakes, else let retry run."""
        status = response.status_code
        if 400 <= status < 500 and status not in RETRYABLE_CLIENT_STATUSES:
            body = (response.text or "").strip()
            raise PermanentHttpError(
                "HTTP {} for {}{}".format(
                    status,
                    redact(url),
                    ": {}".format(body[:300]) if body else "",
                ),
                status_code=status,
                body=body,
            )
        response.raise_for_status()

    def get_text(self, url: str, headers: Optional[dict] = None) -> str:
        def attempt() -> str:
            response = self.client.get(url, headers=headers or None)
            self._check(response, url)
            return response.text

        return retry_call(
            attempt,
            attempts=self.retries,
            backoff_seconds=self.backoff_seconds,
            exceptions=RETRYABLE,
            description="GET {}".format(redact(url)),
        )

    def get_json(self, url: str, headers: Optional[dict] = None):
        def attempt():
            response = self.client.get(url, headers=headers or None)
            self._check(response, url)
            return response.json()

        return retry_call(
            attempt,
            attempts=self.retries,
            backoff_seconds=self.backoff_seconds,
            exceptions=RETRYABLE + (ValueError,),
            description="GET {}".format(redact(url)),
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
