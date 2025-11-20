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

def sanitize_for_logging(value: Any) -> str:
    """
    Sanitize a value for safe logging by removing control characters and Unicode newlines.

    Removes ASCII control characters (C0 and C1 ranges) and Unicode line separators
    to prevent log injection attacks and log corruption. This includes characters
    like newlines, tabs, escape sequences, and other non-printable characters that
    could be used to manipulate log output or inject fake log entries. Additionally,
    removes Unicode line/paragraph separators such as U+2028 and U+2029.

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
        >>> sanitize_for_logging(123)
        '123'
    """
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    value = _CONTROL_CHARS_RE.sub('', value)
    value = _UNICODE_NEWLINES_RE.sub('', value)
    return value



async def get_current_user(request: Request) -> str:
    """Get current user from request state (set by middleware)."""
    return getattr(request.state, 'user_email', 'test@test.com')
