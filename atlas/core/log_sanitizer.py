"""
Minimal utilities for basic chat functionality.
"""

import logging
import re
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f-\x9f]')
# Matches Unicode line separators (LINE SEPARATOR and PARAGRAPH SEPARATOR)
_UNICODE_NEWLINES_RE = re.compile(r'[\u2028\u2029]')
# Matches explicit CR, LF, and CRLF for maximal coverage
_STANDARD_NEWLINES_RE = re.compile(r'(\r\n|\r|\n)')

def sanitize_for_logging(value: Any) -> str:
    """
    Sanitize a value for safe logging by removing ALL newlines (including Unicode and CRLF)
    and control characters, to defend against log injection.

    Removes ASCII control characters (C0 and C1 ranges), CR/LF in any combination,
    and Unicode line/paragraph separators. This includes characters
    like newlines (\\n, \\r, \\r\\n, U+2028, U+2029), tabs, escape sequences, and other
    non-printable characters that could be used to manipulate log output or inject fake log entries.

    Args:
        value: Any value to sanitize. If not a string, it will be converted
               to string representation first.

    Returns:
        str: Sanitized string with all control and newline characters removed.

    Examples:
        >>> sanitize_for_logging("Hello\\nWorld")
        'HelloWorld'
        >>> sanitize_for_logging("Test\\x1b[31mRed\\x1b[0m")
        'TestRed'
        >>> sanitize_for_logging("Fake\u2028Log")
        'FakeLog'
        >>> sanitize_for_logging("line1\\r\\nline2\\rline3\\nline4")
        'line1line2line3line4'
        >>> sanitize_for_logging("A\u2028B\u2029C")
        'ABC'
        >>> sanitize_for_logging(123)
        '123'
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    value = _CONTROL_CHARS_RE.sub('', value)
    value = _UNICODE_NEWLINES_RE.sub('', value)
    value = _STANDARD_NEWLINES_RE.sub('', value)
    return value


def summarize_tool_approval_response_for_logging(data: Any) -> str:
    """Return a non-sensitive summary of a tool approval response payload.

    This is intentionally conservative: it never logs tool argument values or
    rejection reasons because these can contain sensitive user content.

    Expected input shape (from websocket):
        {
          "type": "tool_approval_response",
          "tool_call_id": "...",
          "approved": true/false,
          "arguments": {...},
          "reason": "..."
        }
    """
    if not isinstance(data, dict):
        return f"type=tool_approval_response payload_type={sanitize_for_logging(type(data).__name__)}"

    tool_call_id = sanitize_for_logging(data.get("tool_call_id"))
    approved_raw = data.get("approved", False)
    approved = bool(approved_raw)

    arguments = data.get("arguments")
    has_arguments = arguments is not None
    arguments_count = len(arguments) if isinstance(arguments, dict) else (1 if has_arguments else 0)

    reason = data.get("reason")
    has_reason = bool(reason)

    return (
        "type=tool_approval_response "
        f"tool_call_id={tool_call_id} "
        f"approved={approved} "
        f"has_arguments={has_arguments} "
        f"arguments_count={arguments_count} "
        f"has_reason={has_reason}"
    )



async def get_current_user(request: Request) -> str:
    """Get current user from request state (set by middleware)."""
    return getattr(request.state, 'user_email', 'test@test.com')
