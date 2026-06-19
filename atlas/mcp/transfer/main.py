#!/usr/bin/env python3
"""Local-development MCP server for moving files into and out of a chat session."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("MCP Transfer")


def _base_dir() -> Path:
    """Return the local directory this development server is allowed to access."""
    configured = os.getenv("MCP_TRANSFER_BASE_DIR")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def _resolve_path(path: str) -> Path:
    """Resolve a requested file path and keep it inside the configured base directory."""
    if not path or not path.strip():
        raise ValueError("Path is required")

    base_dir = _base_dir()
    requested = Path(path).expanduser()
    resolved = (requested if requested.is_absolute() else base_dir / requested).resolve()

    try:
        resolved.relative_to(base_dir)
    except ValueError as exc:
        raise PermissionError(f"Access denied: path outside base directory ({base_dir})") from exc

    return resolved


def _relative_path(path: Path) -> str:
    return str(path.relative_to(_base_dir()))


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

    This local-development helper reads a file below `MCP_TRANSFER_BASE_DIR`
    (or the server working directory when unset) and returns it as a base64
    artifact. UTF-8 text files also include decoded text in the tool result.

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
def write_file_to_disk(path: str, content: str, content_is_base64: bool = False) -> Dict[str, Any]:
    """Write content from the chat session to a local file.

    This local-development helper writes below `MCP_TRANSFER_BASE_DIR` (or the
    server working directory when unset). Parent directories are created as
    needed. Set `content_is_base64` to true when writing binary content.

    Args:
        path: Destination file path. Relative paths resolve below the configured base directory.
        content: Text content or base64-encoded bytes to write.
        content_is_base64: Decode `content` as base64 before writing when true.

    Returns:
        MCP tool result with destination path and byte count.
    """
    start = time.perf_counter()
    operation = "write_file_to_disk"

    try:
        file_path = _resolve_path(path)
        if file_path.exists() and file_path.is_dir():
            raise IsADirectoryError(f"Path is a directory: {path}")

        if content_is_base64:
            try:
                file_bytes = base64.b64decode(content, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"Invalid base64 content: {exc}") from exc
        else:
            file_bytes = content.encode("utf-8")

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
