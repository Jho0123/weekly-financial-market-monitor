"""Bounded retry with exponential backoff (spec section 38)."""

import logging
import time
from typing import Callable, Iterable, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryError(Exception):
    """All attempts failed. ``__cause__`` carries the last real error."""


def retry_call(
    func: Callable[[], T],
    attempts: int = 3,
    backoff_seconds: float = 2.0,
    exceptions: Iterable[Type[BaseException]] = (Exception,),
    description: str = "operation",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func`` until it succeeds or ``attempts`` is exhausted.

    Waits ``backoff_seconds * 2**(n-1)`` between attempts: 2s, then 4s,
    then give up. ``sleep`` is injectable so tests do not actually wait.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    catch = tuple(exceptions)
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            return func()
        except catch as exc:
            last_error = exc
            if attempt == attempts:
                break
            delay = backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "%s failed (attempt %d/%d): %s -- retrying in %.1fs",
                description,
                attempt,
                attempts,
                exc,
                delay,
            )
            if delay > 0:
                sleep(delay)

    raise RetryError(
        "{} failed after {} attempt(s): {}".format(description, attempts, last_error)
    ) from last_error
