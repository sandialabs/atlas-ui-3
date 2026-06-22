#!/usr/bin/env python3
"""Local-development MCP server for moving files into and out of a chat session."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit

import requests

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("MCP Transfer")

# Default cap on how many bytes a single read may pull into the chat context.
# Reads larger than this fail in-band so a stray large file cannot blow up
# server memory or the model context. Override with MCP_TRANSFER_MAX_BYTES.
DEFAULT_MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MiB


def _env_flag(name: str, default: bool = False) -> bool:
    """Interpret an environment variable as a boolean flag."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _base_dir() -> Path:
    """Primary root: anchors relative paths, labels result paths, always allowed.

    Defaults to the user's home directory, which keeps normal project work
    (under `~`) flowing while blocking writes to system locations. Override with
    `MCP_TRANSFER_BASE_DIR`.
    """
    configured = os.getenv("MCP_TRANSFER_BASE_DIR")
    return Path(configured).expanduser().resolve() if configured else Path.home().resolve()


def _allowed_roots() -> list[Path]:
    """Every directory file access may touch: the primary root plus extras.

    Additional roots are whitelisted via `MCP_TRANSFER_ALLOWED_DIRS`, an
    `os.pathsep`-separated list (':' on POSIX), so network mounts outside the
    home directory can be opted in explicitly, e.g.
    `MCP_TRANSFER_ALLOWED_DIRS=/projects:/mnt`.
    """
    roots = [_base_dir()]
    extra = os.getenv("MCP_TRANSFER_ALLOWED_DIRS")
    if extra:
        for part in extra.split(os.pathsep):
            if part.strip():
                roots.append(Path(part).expanduser().resolve())
    return roots


def _check_access(resolved: Path) -> None:
    """Enforce the default footgun guards on a resolved path.

    By default access must stay within an allowed root and may not descend into
    a hidden (dotfile / dot-directory) entry below that root — this protects
    credentials and configs like `~/.ssh`, `~/.aws`, and `.env`. Both guards are
    individually relaxable: `MCP_TRANSFER_ALLOW_HIDDEN=true` permits hidden
    paths, and `MCP_TRANSFER_ALLOW_ANY_PATH=true` disables the guards entirely.
    """
    if _env_flag("MCP_TRANSFER_ALLOW_ANY_PATH"):
        return

    roots = _allowed_roots()
    match = None
    for root in roots:
        try:
            match = (root, resolved.relative_to(root))
            break
        except ValueError:
            continue

    if match is None:
        allowed = ", ".join(str(r) for r in roots)
        raise PermissionError(
            f"Access denied: '{resolved}' is outside the allowed root(s) [{allowed}]; "
            "whitelist it via MCP_TRANSFER_ALLOWED_DIRS (os.pathsep-separated) or set "
            "MCP_TRANSFER_ALLOW_ANY_PATH=true to disable this check"
        )

    if not _env_flag("MCP_TRANSFER_ALLOW_HIDDEN"):
        _, rel = match
        hidden = next((part for part in rel.parts if part.startswith(".")), None)
        if hidden:
            raise PermissionError(
                f"Access denied: '{resolved}' is under a hidden path ('{hidden}'); "
                "set MCP_TRANSFER_ALLOW_HIDDEN=true to allow dotfiles/dot-directories"
            )


def _max_read_bytes() -> int:
    """Return the maximum number of bytes a single read may return."""
    configured = os.getenv("MCP_TRANSFER_MAX_BYTES")
    if not configured:
        return DEFAULT_MAX_READ_BYTES
    try:
        value = int(configured)
    except ValueError:
        return DEFAULT_MAX_READ_BYTES
    return value if value > 0 else DEFAULT_MAX_READ_BYTES


def _backend_base_url() -> str:
    """Base URL of the Atlas backend that serves session-file downloads."""
    return os.environ.get(
        "BACKEND_URL", os.environ.get("CHATUI_BACKEND_BASE_URL", "http://localhost:8000")
    )


def _is_backend_download_path(value: str) -> bool:
    """Backend-injected relative download URL for a session file."""
    return value.startswith("/api/files/download/") or value.startswith("/mcp/files/download/")


def _is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _fetch_session_file(filename: str, max_bytes: int) -> Tuple[bytes, str]:
    """Fetch a session file's bytes from a backend-injected download URL.

    The backend rewrites a ``filename`` argument that names a session file into
    a tokenized download URL (relative ``/mcp/files/download/...`` or absolute).
    This streams that URL, enforcing ``max_bytes`` before buffering, and returns
    ``(bytes, display_name)``. The tokenized URL rarely carries the real name, so
    the display name falls back to the URL's last path segment.
    """
    if _is_backend_download_path(filename):
        url = _backend_base_url().rstrip("/") + filename
    elif _is_http_url(filename):
        url = filename
    else:
        raise ValueError(
            f"'{filename}' is not a known session file. Pass the exact name of a "
            "file listed in the session so the backend can resolve it."
        )

    resp = requests.get(url, timeout=30, stream=True)
    resp.raise_for_status()

    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        resp.close()
        raise ValueError(
            f"Session file is too large to transfer: {int(declared)} bytes exceeds "
            f"the {max_bytes}-byte limit (set MCP_TRANSFER_MAX_BYTES to change)"
        )

    buf = bytearray()
    for chunk in resp.iter_content(chunk_size=65536):
        buf.extend(chunk)
        if len(buf) > max_bytes:
            resp.close()
            raise ValueError(
                f"Session file is too large to transfer: exceeds the {max_bytes}-byte "
                "limit (set MCP_TRANSFER_MAX_BYTES to change)"
            )

    name = os.path.basename(urlsplit(url).path) or "file"
    return bytes(buf), name


def _resolve_path(path: str) -> Path:
    """Resolve a requested file path and apply the default access guards.

    Relative paths resolve below the primary root. Symlinks and `..` are
    resolved before the guards run, so neither can escape an allowed root. See
    `_check_access` for the boundary and hidden-path rules and how to relax them.
    """
    if not path or not path.strip():
        raise ValueError("Path is required")

    requested = Path(path).expanduser()
    resolved = (requested if requested.is_absolute() else _base_dir() / requested).resolve()
    _check_access(resolved)
    return resolved


def _relative_path(path: Path) -> str:
    """Display path: relative to the base dir when inside it, else absolute."""
    try:
        return str(path.relative_to(_base_dir()))
    except ValueError:
        return str(path)


def _error_result(operation: str, error: Exception, start: float) -> Dict[str, Any]:
    return {
        "results": {
            "operation": operation,
            "error": str(error),
        },
        "meta_data": {
            "is_error": True,
            "error_type": type(error).__name__,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            "base_dir": str(_base_dir()),
        },
    }


@mcp.tool
def read_file_from_disk(path: str) -> Dict[str, Any]:
    """Read a local file into the chat session as an MCP artifact.

    This local-development helper reads a file and returns it as a base64
    artifact. UTF-8 text files also include decoded text in the tool result.
    Relative paths resolve below the primary root (`MCP_TRANSFER_BASE_DIR`, or
    the home directory when unset); absolute paths are read as given. By default
    access is confined to that root (plus any `MCP_TRANSFER_ALLOWED_DIRS`) and
    hidden dotfiles/dot-directories are blocked — see `_check_access` for the
    `MCP_TRANSFER_ALLOW_HIDDEN` / `MCP_TRANSFER_ALLOW_ANY_PATH` escape hatches.
    Files larger than `MCP_TRANSFER_MAX_BYTES` (default 10 MiB) are rejected so a
    single read cannot load unbounded content into the chat context.

    Args:
        path: File path to read. Relative paths resolve below the configured base directory.

    Returns:
        MCP tool result with file metadata and an artifact containing the file bytes.
    """
    start = time.perf_counter()
    operation = "read_file_from_disk"

    try:
        file_path = _resolve_path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if file_path.is_dir():
            raise IsADirectoryError(f"Path is a directory: {path}")

        max_bytes = _max_read_bytes()
        file_size = file_path.stat().st_size
        if file_size > max_bytes:
            raise ValueError(
                f"File is too large to read: {file_size} bytes exceeds the "
                f"{max_bytes}-byte limit (set MCP_TRANSFER_MAX_BYTES to change)"
            )

        file_bytes = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        artifact = {
            "name": file_path.name,
            "b64": base64.b64encode(file_bytes).decode("ascii"),
            "mime": mime_type,
            "size": len(file_bytes),
            "description": f"Local file read from {_relative_path(file_path)}",
            "viewer": "auto",
        }

        results: Dict[str, Any] = {
            "operation": operation,
            "path": _relative_path(file_path),
            "size_bytes": len(file_bytes),
            "mime_type": mime_type,
            "status": "success",
        }
        try:
            results["content"] = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            results["content_base64"] = artifact["b64"]

        return {
            "results": results,
            "artifacts": [artifact],
            "meta_data": {
                "is_error": False,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "base_dir": str(_base_dir()),
            },
        }
    except Exception as exc:  # noqa: BLE001 - keep MCP tool failures in-band
        return _error_result(operation, exc, start)


@mcp.tool
def write_file_to_disk(
    path: str,
    content: str = "",
    content_is_base64: bool = False,
    filename: str = "",
    original_filename: str = "",
) -> Dict[str, Any]:
    """Write a file from the chat session to a local file.

    Content can come from two sources:

    1. A **session file** produced by another tool (an exported STEP, a generated
       PDF/image, etc.). Pass its name as `filename`; the backend rewrites that to
       a tokenized download URL, and this tool fetches the bytes and writes them
       verbatim. You do not supply `content` in this case.
    2. **Inline content** via `content` -- UTF-8 text, or base64-encoded bytes
       when `content_is_base64` is true.

    Relative paths resolve below the primary root (`MCP_TRANSFER_BASE_DIR`, or
    the home directory when unset); absolute paths are written as given. By
    default access is confined to that root (plus any `MCP_TRANSFER_ALLOWED_DIRS`)
    and hidden dotfiles/dot-directories are blocked — see `_check_access` for the
    `MCP_TRANSFER_ALLOW_HIDDEN` / `MCP_TRANSFER_ALLOW_ANY_PATH` escape hatches.
    Parent directories are created as needed. If `path` names a directory, the
    source file name is appended.

    Args:
        path: Destination file path, or a destination directory (the source file
            name is appended). Relative paths resolve below the configured base directory.
        content: Text content or base64-encoded bytes to write. Ignored when `filename` is given.
        content_is_base64: Decode `content` as base64 before writing when true.
        filename: Name of a session file to transfer to disk. The backend rewrites
            this to a download URL that the tool fetches.
        original_filename: Backend-supplied original name for `filename`; used to
            name the output when `path` is a directory.

    Returns:
        MCP tool result with destination path and byte count.
    """
    start = time.perf_counter()
    operation = "write_file_to_disk"

    try:
        # Resolve the bytes to write. A session file (fetched over HTTP) takes
        # precedence over inline content.
        source_name = ""
        if filename:
            file_bytes, fetched_name = _fetch_session_file(filename, _max_read_bytes())
            source_name = original_filename or fetched_name
        elif content_is_base64:
            try:
                file_bytes = base64.b64decode(content, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"Invalid base64 content: {exc}") from exc
        elif content:
            file_bytes = content.encode("utf-8")
        else:
            raise ValueError("Nothing to write: provide a session 'filename' or 'content'")

        # Allow a directory destination by appending the source file name.
        if not path or not path.strip():
            raise ValueError("Path is required")
        file_path = _resolve_path(path)
        if file_path.is_dir() or path.endswith(("/", os.sep)):
            if not source_name:
                raise ValueError("Destination is a directory; include the file name in 'path'")
            file_path = _resolve_path(str(Path(path) / source_name))

        if file_path.exists() and file_path.is_dir():
            raise IsADirectoryError(f"Path is a directory: {path}")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(file_bytes)

        return {
            "results": {
                "operation": operation,
                "path": _relative_path(file_path),
                "size_bytes": len(file_bytes),
                "status": "success",
            },
            "meta_data": {
                "is_error": False,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
                "base_dir": str(_base_dir()),
            },
        }
    except Exception as exc:  # noqa: BLE001 - keep MCP tool failures in-band
        return _error_result(operation, exc, start)


if __name__ == "__main__":
    mcp.run(show_banner=False)
