"""Normalize the tool-name list that arrives from the wire.

Every mode builds its LLM tool schema from this list, so it is coerced once
here: non-string entries (a malformed client payload) are dropped rather than
becoming schema lookups, and ``None`` degrades to an empty list.
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def normalize_selected_tools(selected_tools: Optional[Sequence[str]]) -> List[str]:
    """Return the wire's tool selection as a clean list of strings."""
    return [t for t in (selected_tools or []) if isinstance(t, str)]