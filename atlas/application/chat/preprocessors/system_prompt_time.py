"""Inject the current date/time (and an elapsed-time note) into the system prompt.

Issue #823: the system prompt should carry the current time so the model knows
what "now" is, and when a meaningful gap has opened between turns, an explicit
note tells the model how much time has elapsed -- a long pause can change what
a correct answer looks like (a status that may have resolved, a deadline that
may have passed).

The current date/time is *always* injected. The elapsed-time note is appended
only when the gap between this turn and the previous user message meets or
exceeds ``refresh_minutes`` (configurable via ``SYSTEM_PROMPT_TIME_REFRESH_MINUTES``;
``0`` disables the note while leaving the current time in place).

The gap is derived from the message timestamps already carried in the
conversation history, so no extra session state is required and the value
survives persistence/reload: the conversation loader preserves original
timestamps, and the current user message is appended to history before
``build_messages`` runs, so the previous turn's user message is the
second-to-last user message in the list.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from atlas.domain.messages.models import MessageRole

logger = logging.getLogger(__name__)

# Deliberately appended *after* the prompt provider has already rendered the
# template (which itself calls ``.format(user_email=...)``). Formatting only
# these sections -- never the caller's prompt text -- means literal braces in
# the rendered prompt cannot be misinterpreted as placeholders.
_TIME_SECTION = "\n\n---\n### Current Date & Time\n{now}\n"
_ELAPSED_SECTION = (
    "\n### Time Since Previous Message\n"
    "Approximately {minutes} minutes have elapsed since the previous message "
    "in this conversation; take the passage of time into account where it is "
    "relevant to your answer.\n"
)


def _resolve_zone(timezone_name: str):
    """Return a ``tzinfo`` for ``timezone_name``, falling back to UTC on error."""
    try:
        if not timezone_name or timezone_name == "UTC":
            return timezone.utc
        return ZoneInfo(timezone_name)
    except Exception:
        logger.warning(
            "Unknown SYSTEM_PROMPT_TIMEZONE %r; falling back to UTC",
            timezone_name,
        )
        return timezone.utc


def _format_now(now_utc: datetime, timezone_name: str) -> str:
    """Render ``now_utc`` in the configured timezone, e.g. '2026-08-21 14:30 MDT'."""
    display = now_utc.astimezone(_resolve_zone(timezone_name))
    return display.strftime("%Y-%m-%d %H:%M %Z").strip()


def _previous_user_timestamp(session: Any) -> Optional[datetime]:
    """Timestamp of the user message before the current one, or ``None``.

    The orchestrator appends the current user message to history before
    ``build_messages`` runs, so the current user message is the last user
    message in the list; the previous turn's is the one before it. Naive
    timestamps (e.g. from an old persisted row) are treated as UTC.
    """
    messages = getattr(getattr(session, "history", None), "messages", None) or []
    user_timestamps = [
        m.timestamp for m in messages
        if getattr(m, "role", None) == MessageRole.USER and getattr(m, "timestamp", None)
    ]
    if len(user_timestamps) < 2:
        return None
    ts = user_timestamps[-2]
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def enrich_system_prompt_with_time(
    system_prompt: str,
    session: Any,
    timezone_name: str,
    refresh_minutes: int,
    now_utc: Optional[datetime] = None,
) -> str:
    """Return ``system_prompt`` with the current date/time (and optionally an
    elapsed-time note) appended.

    Args:
        system_prompt: The rendered system prompt text (default or custom).
        session: Chat session; used to find the previous user message's time.
        timezone_name: IANA timezone name for the displayed time (e.g. ``"UTC"``,
            ``"America/Denver"``). Unknown names fall back to UTC.
        refresh_minutes: Append the elapsed-time note when the gap since the
            previous user message meets/exceeds this many minutes. ``0`` (or
            negative) disables the note; the current date/time is still injected.
        now_utc: Override "now" (for testing). Defaults to ``datetime.now(UTC)``.

    Returns:
        The enriched system prompt. An empty/``None``-ish input is returned as-is.
    """
    if not system_prompt:
        return system_prompt
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    enriched = (
        system_prompt.rstrip()
        + _TIME_SECTION.format(now=_format_now(now, timezone_name))
    )

    if refresh_minutes and refresh_minutes > 0:
        previous = _previous_user_timestamp(session)
        if previous is not None:
            minutes = (now - previous).total_seconds() / 60.0
            if minutes >= refresh_minutes:
                enriched += _ELAPSED_SECTION.format(minutes=int(minutes))

    return enriched
