#!/usr/bin/env python3
"""
File Viewer MCP Server using FastMCP.

Reads a file from the local disk and displays it in the Atlas canvas.

This server is intended for a developer's own machine (single-tenant, local
use) -- NOT for a multi-tenant server deployment. It therefore reads any
absolute or relative path the caller asks for, without sandboxing to a base
directory. Do not deploy it where untrusted users can reach it.

The main job is to guess/normalize the MIME type so the canvas picks a sensible
viewer (image / pdf / html / code), then hand the file back as a v2 artifact.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Annotated, Any, Dict, Optional

import requests

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("File_Viewer")

# Cap how much we'll base64-encode into a single canvas artifact. Big enough for
# typical docs/images, small enough to keep the chat payload sane.
MAX_BYTES = 25 * 1024 * 1024  # 25 MB

# Extensions the stdlib doesn't always know about, plus a few we want to pin to a
# specific type so the viewer mapping below behaves predictably.
_EXTENSION_OVERRIDES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".jsonl": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/plain",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".ts": "text/typescript",
    ".jsx": "text/javascript",
    ".tsx": "text/typescript",
    ".sh": "text/x-shellscript",
    ".html": "text/html",
    ".htm": "text/html",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}

# Magic-number sniffing for the common binary formats, used when the extension
# is missing or unhelpful.
_MAGIC_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _is_backend_download_path(s: str) -> bool:
    """Backend-injected relative download URL (session files)."""
    return s.startswith("/api/files/download/") or s.startswith("/mcp/files/download/")


def _backend_base_url() -> str:
    return os.environ.get(
        "BACKEND_URL", os.environ.get("CHATUI_BACKEND_BASE_URL", "http://localhost:8000")
    )


def _looks_like_text(data: bytes) -> bool:
    """Heuristic: treat as UTF-8 text if it decodes and has no NUL bytes."""
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _normalize_mime(name: str, data: bytes) -> str:
    """Best-effort guess of a file's MIME type.

    Order: explicit extension overrides -> stdlib mimetypes -> magic-number
    sniffing -> text/plain vs application/octet-stream fallback.
    """
    ext = Path(name).suffix.lower()
    if ext in _EXTENSION_OVERRIDES:
        return _EXTENSION_OVERRIDES[ext]

    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed

    for signature, mime in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return mime

    return "text/plain" if _looks_like_text(data) else "application/octet-stream"


def _viewer_hint(mime: str) -> str:
    """Map a MIME type to a canvas viewer hint."""
    if mime.startswith("image/"):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime == "text/html":
        return "html"
    # Everything else (text, json, csv, source code, octet-stream) renders fine
    # in the code/text viewer.
    return "code"


def _read_bytes(source: str) -> tuple[bytes, str]:
    """Load file bytes from a local path or a (backend / http) URL.

    Returns (data, display_name).
    """
    # Backend-injected relative download URL for a session file.
    if _is_backend_download_path(source):
        url = _backend_base_url().rstrip("/") + source
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        # The real filename usually isn't in the tokenized URL; fall back to the
        # last path segment.
        return resp.content, os.path.basename(source.split("?")[0]) or "file"

    if _is_url(source):
        resp = requests.get(source, timeout=30)
        resp.raise_for_status()
        return resp.content, os.path.basename(source.split("?")[0]) or "file"

    # Local path on the developer's machine. Expand ~ and env vars for convenience.
    path = Path(os.path.expandvars(os.path.expanduser(source)))
    if not path.exists():
        raise FileNotFoundError(f"File not found: {source}")
    if path.is_dir():
        raise IsADirectoryError(f"Path is a directory, not a file: {source}")
    return path.read_bytes(), path.name


@mcp.tool
def display_file(
    path: Annotated[
        str,
        "Path to a file on the local disk to open in the canvas. Absolute or "
        "relative paths are accepted; ~ and environment variables are expanded. "
        "A download URL or backend session-file reference also works.",
    ],
) -> Dict[str, Any]:
    """Read a file from the local disk and display it in the Atlas canvas.

    This tool loads a file's raw bytes, guesses and normalizes its MIME type,
    and returns it as a canvas artifact so the user can view it directly. The
    canvas automatically picks a viewer based on the detected type:

    - Images (PNG, JPEG, GIF, SVG, BMP, TIFF) -> image viewer
    - PDFs -> PDF viewer
    - HTML -> sanitized HTML viewer
    - Text, source code, JSON, CSV, YAML, Markdown, logs, etc. -> code/text viewer

    MIME detection uses, in order: a curated extension map, the Python standard
    library, binary magic-number sniffing, and finally a text-vs-binary
    heuristic. This means files with missing or misleading extensions still get
    a reasonable type.

    **Intended for local, single-developer use.** It reads any path it is given
    and does not sandbox access to a base directory.

    Args:
        path: Location of the file to display. A local filesystem path is the
            common case; an http(s) URL or a backend session-file reference is
            also accepted.

    Returns:
        A v2 MCP result with the file as an artifact and a display hint that
        opens the canvas, or an error message if the file cannot be read.
    """
    try:
        data, name = _read_bytes(path)
    except FileNotFoundError as e:
        return {"results": {"error": str(e)}}
    except IsADirectoryError as e:
        return {"results": {"error": str(e)}}
    except requests.HTTPError as e:
        return {"results": {"error": f"Download failed: {e}"}}
    except PermissionError as e:
        return {"results": {"error": f"Permission denied: {e}"}}
    except Exception as e:  # noqa: BLE001
        return {"results": {"error": f"Failed to read file: {e}"}}

    size = len(data)
    if size == 0:
        return {"results": {"error": f"File is empty: {name}"}}
    if size > MAX_BYTES:
        return {
            "results": {
                "error": (
                    f"File is too large to display ({size} bytes, limit "
                    f"{MAX_BYTES} bytes): {name}"
                )
            }
        }

    mime = _normalize_mime(name, data)
    viewer = _viewer_hint(mime)
    b64 = base64.b64encode(data).decode("ascii")

    return {
        "results": {
            "operation": "display_file",
            "filename": name,
            "mime": mime,
            "size": size,
            "message": f"Displaying {name} ({mime}, {size} bytes) in the canvas.",
        },
        "artifacts": [
            {
                "name": name,
                "b64": b64,
                "mime": mime,
                "size": size,
                "description": f"Contents of {name}",
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": name,
            "mode": "replace",
            "viewer_hint": viewer,
        },
        "meta_data": {
            "source": path,
            "detected_mime": mime,
            "viewer_hint": viewer,
        },
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
