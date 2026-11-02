"""Side-effect log naming and parsing, shared by the writer and the reader.

``side_effect_mcp.py`` runs as a standalone script inside each replica's MCP
child and imports this module as a sibling; ``cluster.py`` imports it as part
of the ``harness`` package. Keeping both on one function means a key with
unusual characters can never be written to one file and read from another.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def log_name(key: str) -> str:
    """File name for ``key``: unsafe characters become ``_``."""
    safe = _UNSAFE.sub("_", key)
    if safe in ("", ".", ".."):
        raise ValueError(f"side-effect key {key!r} has no usable characters")
    return safe + ".log"


def read_records(path: Path) -> List[Dict[str, Any]]:
    """Every complete record in ``path``.

    A line without its trailing newline is a write still in flight (or torn by
    a SIGKILL); it and any undecodable line are skipped rather than raised, so
    a count is never masked by a ``JSONDecodeError``.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    records = []
    for raw in data.split(b"\n")[:-1]:
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
