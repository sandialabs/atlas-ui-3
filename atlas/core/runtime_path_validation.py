"""Helpers for validating runtime-managed filesystem paths."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def ensure_writable_directory(
    directory: Path,
    *,
    setting_name: str,
    purpose: str,
) -> Path:
    """Ensure a directory exists and is writable by the current process."""
    directory = directory.expanduser()

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(_format_permission_error(directory, setting_name, purpose, exc)) from exc

    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".atlas-write-test-", delete=True):
            pass
    except OSError as exc:
        raise RuntimeError(_format_permission_error(directory, setting_name, purpose, exc)) from exc

    return directory


def ensure_duckdb_parent_writable(db_url: str, project_root: Path) -> None:
    """Validate the parent directory for a DuckDB URL."""
    if not db_url.startswith("duckdb:///"):
        return

    db_path = db_url.replace("duckdb:///", "", 1)
    resolved_path = Path(db_path)
    if not resolved_path.is_absolute():
        resolved_path = project_root / db_path

    ensure_writable_directory(
        resolved_path.parent,
        setting_name="CHAT_HISTORY_DB_URL",
        purpose=f"DuckDB chat history storage at {resolved_path}",
    )


def _format_permission_error(directory: Path, setting_name: str, purpose: str, exc: OSError) -> str:
    path_hint = os.fspath(directory)
    return (
        f"{purpose} requires a writable directory, but {path_hint!r} is not writable by the current process. "
        f"Check the ownership and permissions for that path or point {setting_name} at a writable location. "
        f"If this path comes from a Docker/Kubernetes bind mount or PVC, fix the host/volume permissions "
        f"(for example via pre-created host directories, fsGroup, or an init container). Original error: {exc}"
    )
