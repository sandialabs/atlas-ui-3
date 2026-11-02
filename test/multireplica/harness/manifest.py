"""Record every process group and container a harness run creates.

When ``ATLAS_MULTIREPLICA_MANIFEST`` names a file, each replica, mock model and
podman container the harness starts is appended to it as one JSON line. The
PR validation script reads it afterwards to prove that exactly those are gone,
without matching unrelated dev servers or containers on the same machine.
"""

from __future__ import annotations

import json
import os


def _append(record: dict) -> None:
    path = os.environ.get("ATLAS_MULTIREPLICA_MANIFEST")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def record_process(role: str, pgid: int) -> None:
    """A child started with ``start_new_session=True`` (its pid is its pgid)."""
    _append({"kind": "process", "role": role, "pgid": pgid})


def record_container(name: str) -> None:
    _append({"kind": "container", "name": name})
