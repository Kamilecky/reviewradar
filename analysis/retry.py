"""
Shared retry/backoff wrapper around a single client.messages.create(...)
call, used by sentiment.py, summarizer.py, and overview.py so all three
Anthropic call sites handle rate limits, transient server errors, and
permanent (config) errors identically.

analysis.sentiment._client() is built with max_retries=0 -- all retry
behavior lives here, not in the SDK's own opaque retry logic, so it can be
logged per-attempt and can distinguish "worth retrying" from "retrying
this would never help" per error category.
"""

import logging
import random
import time
from typing import Callable

import anthropic

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1.0
MAX_DELAY_SECONDS = 20.0

# Status codes where retrying is pointless -- bad key, bad model, malformed
# request, prompt too large. No amount of retrying fixes any of these.
PERMANENT_STATUS_CODES = {400, 401, 403, 404, 413, 422}
# Status codes worth retrying: 429 (rate limit) and 500/529 (server-side,
# transient). Anything else is treated conservatively as non-retryable
# (exhausted immediately) rather than guessed as safe to repeat.
RETRYABLE_STATUS_CODES = {429, 500, 529}


class PermanentAPIError(Exception):
    """
    Raised by call_with_retry for a non-retryable API error (bad API key,
    bad model name, malformed/oversized request). classify_sentiment and
    summarize_pros_cons catch this and return None like any other failure
    (they don't need to distinguish it from a transient failure); overview.py's
    ask_about_product deliberately does NOT catch it, letting it propagate to
    analysis.tasks.generate_product_ai_overview, which does need to tell the
    two apart (see that function's docstring).
    """


def _retry_delay(exc: Exception | None, attempt: int) -> float:
    """
    Honor a Retry-After header if the API sent one; otherwise exponential
    backoff with jitter, capped at MAX_DELAY_SECONDS.
    """
    response = getattr(exc, "response", None)
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass

    backoff = min(BASE_DELAY_SECONDS * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)
    return backoff + random.uniform(0, 1)


def _log_usage(response, *, task_name: str, product_id: int | None) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    from .models import APIUsageLog
    from .usage import log_api_usage

    log_api_usage(
        APIUsageLog.Provider.ANTHROPIC,
        task_name,
        product_id=product_id,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )


def call_with_retry(
    make_request: Callable[[], "anthropic.types.Message"],
    *,
    task_name: str,
    product_id: int | None = None,
):
    """
    Call make_request() (a zero-arg callable wrapping client.messages.create(...))
    with retry/backoff for transient failures, logging token usage on success.

    Returns the raw Message response on success, or None once retries are
    exhausted on a transient error (rate limit, 500/529, connection error).
    Raises PermanentAPIError immediately (no retry) for a non-retryable
    error -- see PermanentAPIError's docstring for why callers differ in
    whether they catch it.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = make_request()
        except anthropic.APIStatusError as exc:
            if exc.status_code in PERMANENT_STATUS_CODES:
                logger.error(
                    "%s: permanent API error (status=%s), not retrying: %s",
                    task_name, exc.status_code, exc,
                )
                raise PermanentAPIError(str(exc)) from exc

            is_retryable = exc.status_code in RETRYABLE_STATUS_CODES
            if not is_retryable or attempt == MAX_RETRIES:
                logger.warning(
                    "%s: giving up after %s/%s attempts (status=%s): %s",
                    task_name, attempt, MAX_RETRIES, exc.status_code, exc,
                )
                return None

            delay = _retry_delay(exc, attempt)
            logger.warning(
                "%s: retryable error on attempt %s/%s (status=%s), retrying in %.1fs: %s",
                task_name, attempt, MAX_RETRIES, exc.status_code, delay, exc,
            )
            time.sleep(delay)
            continue
        except anthropic.APIConnectionError as exc:
            if attempt == MAX_RETRIES:
                logger.warning(
                    "%s: giving up after %s/%s attempts (connection error): %s",
                    task_name, attempt, MAX_RETRIES, exc,
                )
                return None

            delay = _retry_delay(None, attempt)
            logger.warning(
                "%s: connection error on attempt %s/%s, retrying in %.1fs: %s",
                task_name, attempt, MAX_RETRIES, delay, exc,
            )
            time.sleep(delay)
            continue
        else:
            _log_usage(response, task_name=task_name, product_id=product_id)
            return response

    return None  # unreachable in practice; defensive fallback
