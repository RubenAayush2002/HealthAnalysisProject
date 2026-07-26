"""
retry.py

Shared tenacity retry policy for external calls (Gemini, Tavily). Retries
only transient failures (timeouts, rate limits, 5xx) with exponential
backoff + jitter. Deliberately does NOT retry validation errors or
malformed-request errors -- those fail identically every time, so retrying
them just burns time and quota.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

T = TypeVar("T")

_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "rate limit",
    "rate_limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "unavailable",
    "connection",
)


def is_transient_error(exc: BaseException) -> bool:
    """
    Heuristic classification: treat an error as transient/retryable only if
    its type or message suggests a timeout, rate limit, or 5xx-style
    availability issue. Validation errors (e.g. pydantic ValidationError)
    and malformed-request errors are never retried.
    """
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return False

    message = str(exc).lower()
    type_name = type(exc).__name__.lower()
    haystack = f"{type_name} {message}"
    return any(marker in haystack for marker in _TRANSIENT_MARKERS)


def external_call_retry(func: Callable[..., T]) -> Callable[..., T]:
    """Decorator applying the standard retry policy to an external call."""
    return retry(
        retry=retry_if_exception(is_transient_error),
        wait=wait_exponential_jitter(initial=1, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )(func)
