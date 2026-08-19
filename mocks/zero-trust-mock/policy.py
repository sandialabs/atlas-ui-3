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

import json
from typing import Any, Dict, Optional, Tuple

DENY_TERMS = ("bomb", "gun", "weapon", "explosive", "malware")
ASK_TERMS = ("password", "credential", "secret", "production", "delete")


def flatten(value: Any) -> str:
    """Lower-cased text of every string in a payload, keys included.

    Serializing the payload means a term is caught wherever it appears -- a
    tool argument, a nested object, a prompt, a retrieved chunk -- instead of
    only in the one field a hand-written check happened to look at.
    """
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, default=str).lower()


def find_term(text: str, terms: Tuple[str, ...]) -> Optional[str]:
    for term in terms:
        if term in text:
            return term
    return None


def evaluate(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Return a hook decision dict for one envelope.

    The returned shape is exactly what a hook prints on stdout:
    ``{"decision": "continue"|"require_approval"|"deny", "reason": str}``.
    """
    payload = envelope.get("payload") or {}
    subject = envelope.get("user_email") or "unknown"
    text = flatten(payload)

    denied = find_term(text, DENY_TERMS)
    if denied:
        return {
            "decision": "deny",
            "reason": f"Zero-trust policy: request mentions {denied!r} and is blocked.",
        }

    asked = find_term(text, ASK_TERMS)
    if asked:
        return {
            "decision": "require_approval",
            "reason": f"Zero-trust policy: {asked!r} is sensitive -- {subject} must confirm.",
        }

    return {"decision": "continue", "reason": "Zero-trust policy: no matching term."}
