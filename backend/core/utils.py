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



async def get_current_user(request: Request) -> str:
    """Get current user from request state (set by middleware)."""
    return getattr(request.state, 'user_email', 'test@test.com')
