#!/usr/bin/env python3
"""The whole policy, in one function, so it can be read and unit-tested.

Two keyword sets over a flattened view of the hook envelope's payload:

  * ``DENY_TERMS``  -> ``deny``             (the call never runs)
  * ``ASK_TERMS``   -> ``require_approval`` (an otherwise-permitted call is
                                             escalated to the human gate)

Everything else continues. Real deployments would consult a policy engine here;
keyword matching keeps the demo readable.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, Optional, Tuple

DENY_TERMS = ("bomb", "gun", "weapon", "explosive", "malware")
ASK_TERMS = ("password", "credential", "secret", "production", "delete")


def iter_values(value: Any) -> Iterator[str]:
    """Yield every string *value* in a payload, at any depth.

    Walking the structure means a term is caught wherever it appears -- a tool
    argument, a nested object, a prompt, a retrieved chunk -- instead of only in
    the one field a hand-written check happened to look at. Keys are skipped:
    a field literally named ``password`` is a schema detail, not a signal about
    what this call is doing.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_values(item)
    elif value is not None:
        yield str(value)


def find_term(values: Tuple[str, ...], terms: Tuple[str, ...]) -> Optional[str]:
    """Return the first term appearing as a whole word in any value.

    Word boundaries, not substrings: ``pip install gunicorn`` is not a request
    about a gun, and ``secretary`` is not a secret. A demo that cannot tell the
    difference teaches the wrong lesson about keyword policies.
    """
    for term in terms:
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        if any(pattern.search(value) for value in values):
            return term
    return None


def evaluate(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Return a hook decision dict for one envelope.

    The returned shape is exactly what a hook prints on stdout:
    ``{"decision": "continue"|"require_approval"|"deny", "reason": str}``.
    """
    payload = envelope.get("payload") or {}
    subject = envelope.get("user_email") or "unknown"
    values = tuple(iter_values(payload))

    denied = find_term(values, DENY_TERMS)
    if denied:
        return {
            "decision": "deny",
            "reason": f"Zero-trust policy: request mentions {denied!r} and is blocked.",
        }

    asked = find_term(values, ASK_TERMS)
    if asked:
        return {
            "decision": "require_approval",
            "reason": f"Zero-trust policy: {asked!r} is sensitive -- {subject} must confirm.",
        }

    return {"decision": "continue", "reason": "Zero-trust policy: no matching term."}
