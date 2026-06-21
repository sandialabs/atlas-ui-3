"""Tests for the local-development MCP file_viewer example server.

Lives under atlas/tests/ so it is collected by the CI suite
(`pytest tests` run from the atlas/ directory). The file_viewer example is
imported with a stubbed server factory so the FastMCP dependency is not
required to exercise the tool functions.
"""

import base64
import importlib
import sys
import types

import pytest


class _DummyMCP:
    def tool(self, func=None):
        if func is None:
            return lambda wrapped: wrapped
        return func


def _load_module(monkeypatch):
    fake_factory = types.ModuleType("atlas.mcp_shared.server_factory")
    fake_factory.create_stdio_server = lambda name: _DummyMCP()
    monkeypatch.setitem(sys.modules, "atlas.mcp_shared.server_factory", fake_factory)
    sys.modules.pop("atlas.mcp.file_viewer.main", None)
    return importlib.import_module("atlas.mcp.file_viewer.main")


# --- MIME normalization ----------------------------------------------------

@pytest.mark.parametrize(
    "name,data,expected_mime,expected_viewer",
    [
        # Extension map wins.
        ("notes.md", b"# hi", "text/markdown", "code"),
        ("data.json", b"{}", "application/json", "code"),
        ("page.html", b"<html>", "text/html", "html"),
        ("vector.svg", b"<svg>", "image/svg+xml", "image"),
        # Magic-number sniffing when the extension is missing/unhelpful.
        ("screenshot", b"\x89PNG\r\n\x1a\n\x00\x00", "image/png", "image"),
        ("scan", b"%PDF-1.7 ...", "application/pdf", "pdf"),
        ("photo", b"\xff\xd8\xff\xe0blob", "image/jpeg", "image"),
        # Text-vs-binary heuristic fallback.
        ("mystery", b"just plain text", "text/plain", "code"),
        ("blob.bin", b"\x00\x01\x02\x03", "application/octet-stream", "code"),
    ],
)
def test_mime_and_viewer(monkeypatch, name, data, expected_mime, expected_viewer):
    fv = _load_module(monkeypatch)
    mime = fv._normalize_mime(name, data)
    assert mime == expected_mime
    assert fv._viewer_hint(mime) == expected_viewer


# --- display_file end-to-end ----------------------------------------------

def test_display_file_text(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    f = tmp_path / "hello.md"
    f.write_text("# Title\nbody", encoding="utf-8")

    result = fv.display_file(str(f))

    assert "error" not in result["results"]
    assert result["results"]["mime"] == "text/markdown"
    art = result["artifacts"][0]
    assert art["name"] == "hello.md"
    assert art["mime"] == "text/markdown"
    # viewer must be present so the streaming progress_artifacts path renders it.
    assert art["viewer"] == "code"
    assert base64.b64decode(art["b64"]) == b"# Title\nbody"
    assert result["display"]["open_canvas"] is True
    assert result["display"]["primary_file"] == "hello.md"
    assert result["display"]["viewer_hint"] == "code"


def test_display_file_extensionless_png(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    f = tmp_path / "screenshot"  # no extension
    f.write_bytes(png)

    result = fv.display_file(str(f))

    assert result["results"]["mime"] == "image/png"
    assert result["artifacts"][0]["viewer"] == "image"
    assert result["display"]["viewer_hint"] == "image"


def test_display_file_size_cap_uses_stat(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    monkeypatch.setattr(fv, "MAX_BYTES", 8)
    f = tmp_path / "big.txt"
    f.write_text("this is more than eight bytes", encoding="utf-8")

    result = fv.display_file(str(f))

    assert "too large" in result["results"]["error"]


def test_display_file_empty(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    f = tmp_path / "empty.txt"
    f.write_bytes(b"")

    result = fv.display_file(str(f))

    assert "empty" in result["results"]["error"].lower()


def test_display_file_missing(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    result = fv.display_file(str(tmp_path / "nope.xyz"))
    assert "not found" in result["results"]["error"].lower()


def test_display_file_directory(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    result = fv.display_file(str(tmp_path))
    assert "directory" in result["results"]["error"].lower()
