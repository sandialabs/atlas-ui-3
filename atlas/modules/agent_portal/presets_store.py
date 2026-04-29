"""Server-side JSON store for Agent Portal launch presets.

A preset captures the full launch-form payload (command, args, cwd,
sandbox settings, resource limits) under a human-chosen name so the
user can rehydrate it into the form and launch without retyping.

Storage layout
--------------
All presets for every user live in a single JSON file at
``<app_config_dir>/agent_portal_presets.json``:

    {
        "schema_version": 1,
        "presets": [ {...}, {...}, ... ]
    }

Each entry carries a ``user_email`` field; the store always filters by
owner on read and write so one user cannot see, edit, or delete
another user's presets. (The per-process endpoints still defer
ownership checks to graduation — see docs/agentportal/threat-model.md.)

Concurrency
-----------
Writes go through a temp-file + rename atomic swap, and the whole
read-modify-write cycle is serialized by ``fcntl.flock`` on a sibling
``.lock`` file. On a single-dev box concurrent writes are unlikely,
but the lock is cheap insurance against two browser tabs racing.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional

from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_FILE_NAME = "agent_portal_presets.json"
_LOCK_FILE_NAME = "agent_portal_presets.lock"


class PresetNotFoundError(Exception):
    """Raised when a preset id does not exist for the requesting user."""


@dataclass
class Preset:
    id: str
    user_email: str
    name: str
    command: str
    description: str = ""
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    sandbox_mode: str = "off"
    extra_writable_paths: List[str] = field(default_factory=list)
    use_pty: bool = False
    namespaces: bool = False
    isolate_network: bool = False
    memory_limit: Optional[str] = None
    cpu_limit: Optional[str] = None
    pids_limit: Optional[int] = None
    display_name: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_public(self) -> dict:
        """Serialize for HTTP responses. Drops no fields; user_email is fine
        to surface since the caller is always the owner."""
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict) -> "Preset":
        # Be tolerant of extra/missing keys so a future schema bump doesn't
        # break older files. Unknown keys are silently dropped.
        kwargs = {}
        for f in cls.__dataclass_fields__:
            if f in row:
                kwargs[f] = row[f]
        return cls(**kwargs)


def _resolve_config_dir() -> Path:
    """Resolve the user-config directory to an absolute path.

    Mirrors the logic in ``ConfigManager._search_paths``: if
    ``APP_CONFIG_DIR`` is relative, resolve it against the project root.
    """
    cm = app_factory.get_config_manager()
    raw = Path(cm.app_settings.app_config_dir)
    if raw.is_absolute():
        return raw
    project_root = cm._atlas_root.parent
    return project_root / raw


class PresetStore:
    """JSON-backed preset CRUD with per-user filtering and atomic writes."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            config_dir = _resolve_config_dir()
            self._path = config_dir / _FILE_NAME
            self._lock_path = config_dir / _LOCK_FILE_NAME
        else:
            self._path = path
            self._lock_path = path.with_suffix(path.suffix + ".lock")

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _load_all(self) -> List[dict]:
        """Read the full preset list from disk (unfiltered). Empty on miss."""
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(
                "agent_portal presets file unreadable at %s: %s",
                self._path,
                exc,
            )
            return []
        if not isinstance(data, dict):
            return []
        presets = data.get("presets")
        if not isinstance(presets, list):
            return []
        return [p for p in presets if isinstance(p, dict)]

    def _write_all(self, rows: List[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {"schema_version": SCHEMA_VERSION, "presets": rows}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)

    class _FileLock:
        """Context manager around ``fcntl.flock`` on a sibling lock file."""

        def __init__(self, lock_path: Path):
            self._lock_path = lock_path
            self._fd: Optional[int] = None

        def __enter__(self):
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT
            self._fd = os.open(self._lock_path, flags, 0o600)
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except OSError:
                os.close(self._fd)
                self._fd = None
                raise
            return self

        def __exit__(self, exc_type, exc, tb):
            if self._fd is not None:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                finally:
                    try:
                        os.close(self._fd)
                    except OSError as e:
                        if e.errno != errno.EBADF:
                            raise
                    self._fd = None

    # ------------------------------------------------------------------
    # CRUD (per-user filtered)
    # ------------------------------------------------------------------

    def list_for_user(self, user_email: str) -> List[Preset]:
        rows = self._load_all()
        out: List[Preset] = []
        for row in rows:
            if row.get("user_email") != user_email:
                continue
            try:
                out.append(Preset.from_row(row))
            except TypeError:
                continue
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out

    def get(self, preset_id: str, user_email: str) -> Preset:
        for p in self.list_for_user(user_email):
            if p.id == preset_id:
                return p
        raise PresetNotFoundError(preset_id)

    def create(self, data: Dict, user_email: str) -> Preset:
        now = time.time()
        preset = Preset(
            id=f"pst_{uuid.uuid4().hex}",
            user_email=user_email,
            name=data.get("name", "").strip() or "Untitled",
            description=data.get("description", "") or "",
            command=data.get("command", ""),
            args=list(data.get("args") or []),
            cwd=data.get("cwd"),
            sandbox_mode=data.get("sandbox_mode", "off"),
            extra_writable_paths=list(data.get("extra_writable_paths") or []),
            use_pty=bool(data.get("use_pty", False)),
            namespaces=bool(data.get("namespaces", False)),
            isolate_network=bool(data.get("isolate_network", False)),
            memory_limit=data.get("memory_limit"),
            cpu_limit=data.get("cpu_limit"),
            pids_limit=data.get("pids_limit"),
            display_name=data.get("display_name"),
            created_at=now,
            updated_at=now,
        )
        with self._FileLock(self._lock_path):
            rows = self._load_all()
            rows.append(asdict(preset))
            self._write_all(rows)
        return preset

    def update(self, preset_id: str, data: Dict, user_email: str) -> Preset:
        with self._FileLock(self._lock_path):
            rows = self._load_all()
            for idx, row in enumerate(rows):
                if row.get("id") == preset_id and row.get("user_email") == user_email:
                    existing = Preset.from_row(row)
                    # Only apply keys the caller actually sent; treat others
                    # as unchanged. Immutable fields (id, user_email,
                    # created_at) are never overwritten.
                    mutable = {
                        k: v for k, v in data.items()
                        if k in Preset.__dataclass_fields__
                        and k not in ("id", "user_email", "created_at")
                        and v is not None
                    }
                    # Strings we want to allow empty-string, but None means
                    # "no change" — already filtered above.
                    updated = replace(existing, **mutable, updated_at=time.time())
                    rows[idx] = asdict(updated)
                    self._write_all(rows)
                    return updated
        raise PresetNotFoundError(preset_id)

    def delete(self, preset_id: str, user_email: str) -> None:
        with self._FileLock(self._lock_path):
            rows = self._load_all()
            kept = [
                r for r in rows
                if not (r.get("id") == preset_id and r.get("user_email") == user_email)
            ]
            if len(kept) == len(rows):
                raise PresetNotFoundError(preset_id)
            self._write_all(kept)


_singleton: Optional[PresetStore] = None


def get_preset_store() -> PresetStore:
    global _singleton
    if _singleton is None:
        _singleton = PresetStore()
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test-only helper; do not call from production code."""
    global _singleton
    _singleton = None
