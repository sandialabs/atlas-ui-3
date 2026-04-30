"""Workspace-bounded file operations.

All paths are resolved relative to the session workspace and must
``Path.resolve()`` to a location strictly inside it -- this defeats
path-traversal via ``..`` and via symlinks.
"""

from __future__ import annotations

import base64
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class WorkspaceError(Exception):
    """Raised on any unsafe path or violated cap."""


def _safe_path(workspace: Path, rel: str) -> Path:
    """Resolve ``rel`` inside ``workspace`` or raise WorkspaceError."""
    workspace = workspace.resolve()
    if not rel:
        return workspace
    candidate = (workspace / rel).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as e:
        raise WorkspaceError(f"path escapes workspace: {rel!r}") from e
    return candidate


def workspace_bytes_used(workspace: Path) -> int:
    workspace = workspace.resolve()
    total = 0
    for root, _, files in os.walk(workspace):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def list_dir(workspace: Path, rel: str = "") -> List[Dict[str, object]]:
    target = _safe_path(workspace, rel)
    if not target.exists():
        raise WorkspaceError(f"not found: {rel!r}")
    if not target.is_dir():
        raise WorkspaceError(f"not a directory: {rel!r}")
    out: List[Dict[str, object]] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name):
        try:
            st = child.stat()
        except OSError:
            continue
        out.append({
            "name": child.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "is_dir": child.is_dir(),
        })
    return out


def read_file(
    workspace: Path,
    rel: str,
    *,
    max_bytes: int,
    encoding: Optional[str] = "utf-8",
) -> Tuple[str, bool]:
    """Return ``(content, is_base64)``.

    If ``encoding`` is not None, the file is decoded with that encoding
    (UTF-8 default). On decode error or ``encoding=None`` the raw bytes
    are base64-encoded.
    """
    target = _safe_path(workspace, rel)
    if not target.is_file():
        raise WorkspaceError(f"not a file: {rel!r}")
    size = target.stat().st_size
    if size > max_bytes:
        raise WorkspaceError(
            f"file too large: {size} bytes > max {max_bytes}"
        )
    data = target.read_bytes()
    if encoding is None:
        return base64.b64encode(data).decode("ascii"), True
    try:
        return data.decode(encoding), False
    except UnicodeDecodeError:
        return base64.b64encode(data).decode("ascii"), True


def write_file(
    workspace: Path,
    rel: str,
    *,
    content: Optional[str] = None,
    content_base64: Optional[str] = None,
    workspace_cap_bytes: int,
) -> int:
    if (content is None) == (content_base64 is None):
        raise WorkspaceError(
            "exactly one of content / content_base64 must be provided"
        )
    target = _safe_path(workspace, rel)
    if target == workspace.resolve():
        raise WorkspaceError("cannot write workspace root")
    if content is not None:
        data = content.encode("utf-8")
    else:
        try:
            data = base64.b64decode(content_base64, validate=True)
        except Exception as e:
            raise WorkspaceError(f"invalid base64 content: {e}") from e

    # Cap check (current usage minus file-being-overwritten + new size)
    current = workspace_bytes_used(workspace)
    existing = target.stat().st_size if target.exists() else 0
    projected = current - existing + len(data)
    if projected > workspace_cap_bytes:
        raise WorkspaceError(
            f"workspace cap exceeded: would use {projected} bytes "
            f"> cap {workspace_cap_bytes}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return len(data)


def delete_path(workspace: Path, rel: str) -> Dict[str, object]:
    target = _safe_path(workspace, rel)
    if target == workspace.resolve():
        raise WorkspaceError("cannot delete workspace root (use reset_session)")
    if not target.exists():
        raise WorkspaceError(f"not found: {rel!r}")
    if target.is_dir():
        shutil.rmtree(target)
        return {"deleted": rel, "kind": "dir"}
    target.unlink()
    return {"deleted": rel, "kind": "file"}
