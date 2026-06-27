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


# --- display_folder_files end-to-end ---------------------------------------

def test_display_folder_files_default_level(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    root_file = tmp_path / "root.md"
    root_file.write_text("# Root", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.txt").write_text("nested", encoding="utf-8")

    result = fv.display_folder_files(str(tmp_path))

    assert "error" not in result["results"]
    assert result["results"]["operation"] == "display_folder_files"
    assert result["results"]["level"] == 1
    assert result["results"]["file_count"] == 1
    assert result["results"]["files"][0]["path"] == "root.md"
    assert result["artifacts"][0]["name"] == "root.md"
    assert base64.b64decode(result["artifacts"][0]["b64"]) == b"# Root"
    assert result["display"]["primary_file"] == "root.md"


def test_display_folder_files_level_includes_children(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    (tmp_path / "root.md").write_text("# Root", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.json").write_text("{}", encoding="utf-8")
    (child / "empty.txt").write_text("", encoding="utf-8")
    grandchild = child / "grandchild"
    grandchild.mkdir()
    (grandchild / "too-deep.txt").write_text("too deep", encoding="utf-8")

    result = fv.display_folder_files(str(tmp_path), level=2)

    assert result["results"]["file_count"] == 2
    assert result["results"]["skipped_count"] == 1
    assert [file["path"] for file in result["results"]["files"]] == [
        "root.md",
        "child/nested.json",
    ]
    assert [artifact["name"] for artifact in result["artifacts"]] == [
        "root.md",
        "child_nested.json",
    ]
    assert "too-deep.txt" not in [file["path"] for file in result["results"]["files"]]
    assert result["display"]["primary_file"] == "root.md"


def test_display_folder_files_errors(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory", encoding="utf-8")

    missing = fv.display_folder_files(str(tmp_path / "missing"))
    not_directory = fv.display_folder_files(str(file_path))
    bad_level = fv.display_folder_files(str(tmp_path), level=0)

    assert "not found" in missing["results"]["error"].lower()
    assert "not a directory" in not_directory["results"]["error"].lower()
    assert "at least 1" in bad_level["results"]["error"]


def test_display_folder_files_file_count_cap(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    monkeypatch.setattr(fv, "MAX_FOLDER_FILES", 2)
    for i in range(5):
        (tmp_path / f"file{i}.txt").write_text(f"content {i}", encoding="utf-8")

    result = fv.display_folder_files(str(tmp_path))

    assert result["results"]["file_count"] == 2
    assert result["results"]["truncated"] is True
    assert result["results"]["omitted_count"] == 3
    assert len(result["artifacts"]) == 2
    assert "display limit" in result["results"]["message"].lower()


def test_display_folder_files_total_byte_cap(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    monkeypatch.setattr(fv, "MAX_FOLDER_TOTAL_BYTES", 10)
    (tmp_path / "a.txt").write_text("12345", encoding="utf-8")  # 5 bytes
    (tmp_path / "b.txt").write_text("12345", encoding="utf-8")  # 5 bytes -> total 10
    (tmp_path / "c.txt").write_text("1", encoding="utf-8")  # would exceed 10

    result = fv.display_folder_files(str(tmp_path))

    assert result["results"]["file_count"] == 2
    assert result["results"]["truncated"] is True
    assert result["results"]["omitted_count"] == 1


def test_display_folder_files_name_collision_suffixed(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    # Root file whose name equals the sanitized form of child/nested.json.
    (tmp_path / "child_nested.json").write_text("root", encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    (child / "nested.json").write_text("nested", encoding="utf-8")

    result = fv.display_folder_files(str(tmp_path), level=2)

    names = [artifact["name"] for artifact in result["artifacts"]]
    # Both files are displayed and the colliding name is uniquified.
    assert len(names) == 2
    assert len(set(names)) == 2
    assert "child_nested.json" in names
    assert "child_nested_1.json" in names


def test_display_folder_files_skips_hidden_and_ignored(monkeypatch, tmp_path):
    fv = _load_module(monkeypatch)
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".secret").write_text("secret", encoding="utf-8")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]", encoding="utf-8")
    vendor = tmp_path / "node_modules"
    vendor.mkdir()
    (vendor / "pkg.js").write_text("module.exports = {}", encoding="utf-8")

    result = fv.display_folder_files(str(tmp_path), level=2)

    paths = [file["path"] for file in result["results"]["files"]]
    assert paths == ["visible.txt"]
    skipped_paths = [entry["path"] for entry in result["results"]["skipped"]]
    # Hidden file is reported as skipped; pruned directories don't appear at all.
    assert ".secret" in skipped_paths
    assert not any(".git" in p for p in paths)
    assert not any("node_modules" in p for p in paths)
