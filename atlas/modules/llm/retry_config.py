"""Retry policy for transient LLM failures (issue #919).

Shared by the non-streaming caller (``litellm_caller._acompletion_with_retry``)
and the streaming generators (``litellm_streaming``), which cannot import from
``litellm_caller`` because that module imports the streaming mixin.

The number of retries and the total backoff budget come from the environment
(``.env``) so operators can tune them without a code change:

- ``LLM_MAX_RETRIES``: retries *after the first attempt* (default 5, so up to
  6 calls). ``0`` disables retries entirely.
- ``LLM_RETRY_MAX_WAIT_SECONDS``: cap on the *cumulative* time spent asleep
  between attempts (default 300s = 5 minutes). Matters when the retry count
  is raised: without a cap, attempt 9 alone would wait 4+ minutes.

Delays use exponential backoff (base 1s, doubling per attempt) with up to
0.5s of jitter to avoid synchronized retry storms across concurrent users.
"""

import logging
import os
import random
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_LLM_RETRIES = 5
DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS = 300.0
RETRY_BASE_DELAY_SECONDS = 1.0


def _llm_retry_settings() -> Tuple[int, float]:
    """Resolve (max_retries, max_wait_seconds) from the environment.

    Read at call time so a `.env` change plus restart takes effect without a
    code edit, and so tests can set the variables per-test. A non-numeric
    value falls back to the default; a negative value is clamped to 0 rather
    than silently reinterpreted.
    """
    try:
        max_retries = int(os.environ.get("LLM_MAX_RETRIES", DEFAULT_LLM_RETRIES))
    except (TypeError, ValueError):
        logger.warning(
            "LLM_MAX_RETRIES=%r is not an integer; using default %d",
            os.environ.get("LLM_MAX_RETRIES"), DEFAULT_LLM_RETRIES,
        )
        max_retries = DEFAULT_LLM_RETRIES
    try:
        max_wait = float(
            os.environ.get("LLM_RETRY_MAX_WAIT_SECONDS", DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS)
        )
    except (TypeError, ValueError):
        logger.warning(
            "LLM_RETRY_MAX_WAIT_SECONDS=%r is not a number; using default %.0f",
            os.environ.get("LLM_RETRY_MAX_WAIT_SECONDS"),
            DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS,
        )
        max_wait = DEFAULT_LLM_RETRY_MAX_WAIT_SECONDS
    return max(0, max_retries), max(0.0, max_wait)


def _retry_backoff_delay(attempt: int, max_wait: float, waited: float) -> Optional[float]:
    """Next exponential-backoff delay, clamped to the remaining wait budget.

    Returns ``None`` once the cumulative wait has reached ``max_wait`` -- the
    caller must then give up instead of sleeping.
    """
    remaining = max_wait - waited
    if remaining <= 0:
        return None
    raw = RETRY_BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
    return min(raw, remaining)
