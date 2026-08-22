"""Tests for the local-development MCP transfer example server.

Lives under atlas/tests/ so it is collected by the CI suite
(`pytest tests` run from the atlas/ directory). The transfer example is
imported with a stubbed server factory so the FastMCP dependency is not
required to exercise the tool functions.
"""

import base64
import importlib
import os
import sys
import types


class _DummyMCP:
    def tool(self, func=None):
        if func is None:
            return lambda wrapped: wrapped
        return func


def _load_transfer_module(monkeypatch):
    fake_factory = types.ModuleType("atlas.mcp_shared.server_factory")
    fake_factory.create_stdio_server = lambda name: _DummyMCP()
    monkeypatch.setitem(sys.modules, "atlas.mcp_shared.server_factory", fake_factory)
    sys.modules.pop("atlas.mcp.transfer.main", None)
    return importlib.import_module("atlas.mcp.transfer.main")


def test_write_and_read_text_file(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    write_result = transfer.write_file_to_disk("notes/example.txt", "hello from chat")

    assert write_result["meta_data"]["is_error"] is False
    assert write_result["results"]["path"] == "notes/example.txt"
    assert (tmp_path / "notes" / "example.txt").read_text(encoding="utf-8") == "hello from chat"

    read_result = transfer.read_file_from_disk("notes/example.txt")

    assert read_result["meta_data"]["is_error"] is False
    assert read_result["results"]["content"] == "hello from chat"
    assert read_result["artifacts"][0]["name"] == "example.txt"
    assert base64.b64decode(read_result["artifacts"][0]["b64"]) == b"hello from chat"


def test_write_base64_file(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    encoded = base64.b64encode(b"\x00\x01binary").decode("ascii")
    result = transfer.write_file_to_disk("binary.dat", encoded, content_is_base64=True)

    assert result["meta_data"]["is_error"] is False
    assert (tmp_path / "binary.dat").read_bytes() == b"\x00\x01binary"


def _clear_guard_env(monkeypatch):
    for name in (
        "MCP_TRANSFER_ALLOWED_DIRS",
        "MCP_TRANSFER_ALLOW_HIDDEN",
        "MCP_TRANSFER_ALLOW_ANY_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_path_outside_root_denied_by_default(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(base))
    _clear_guard_env(monkeypatch)

    outside = tmp_path / "elsewhere" / "out.txt"
    result = transfer.write_file_to_disk(str(outside), "blocked")

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "PermissionError"
    assert "outside the allowed root" in result["results"]["error"]
    assert not outside.exists()


def test_allow_any_path_permits_outside_root(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(base))
    _clear_guard_env(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_ALLOW_ANY_PATH", "true")

    outside = tmp_path / "elsewhere" / "out.txt"
    result = transfer.write_file_to_disk(str(outside), "anywhere")

    assert result["meta_data"]["is_error"] is False
    assert outside.read_text(encoding="utf-8") == "anywhere"
    # Outside the primary root, the path is reported as its absolute location.
    assert result["results"]["path"] == str(outside)


def test_allowed_dirs_whitelists_extra_root(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    base = tmp_path / "home"
    base.mkdir()
    mount = tmp_path / "mnt" / "projects"
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(base))
    _clear_guard_env(monkeypatch)
    # Mimic whitelisting a network mount outside home.
    monkeypatch.setenv("MCP_TRANSFER_ALLOWED_DIRS", f"{tmp_path / 'mnt'}{os.pathsep}/nonexistent")

    target = mount / "out.txt"
    result = transfer.write_file_to_disk(str(target), "on the mount")

    assert result["meta_data"]["is_error"] is False
    assert target.read_text(encoding="utf-8") == "on the mount"


def test_hidden_path_denied_by_default(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    _clear_guard_env(monkeypatch)

    # A dotfile and a dot-directory below the root are both blocked.
    for target in (".env", ".ssh/id_rsa"):
        result = transfer.write_file_to_disk(target, "secret")
        assert result["meta_data"]["is_error"] is True, target
        assert result["meta_data"]["error_type"] == "PermissionError"
        assert "hidden path" in result["results"]["error"]

    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".ssh" / "id_rsa").exists()


def test_allow_hidden_permits_dotfiles(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    _clear_guard_env(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_ALLOW_HIDDEN", "true")

    result = transfer.write_file_to_disk(".env", "KEY=value")

    assert result["meta_data"]["is_error"] is False
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "KEY=value"


def test_home_is_default_root(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("MCP_TRANSFER_BASE_DIR", raising=False)
    _clear_guard_env(monkeypatch)
    monkeypatch.setenv("HOME", str(home))

    ok = transfer.write_file_to_disk(str(home / "notes.txt"), "in home")
    assert ok["meta_data"]["is_error"] is False
    assert (home / "notes.txt").read_text(encoding="utf-8") == "in home"

    denied = transfer.write_file_to_disk(str(tmp_path / "outside.txt"), "nope")
    assert denied["meta_data"]["is_error"] is True
    assert denied["meta_data"]["error_type"] == "PermissionError"


def test_read_rejects_files_over_size_cap(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_MAX_BYTES", "16")

    (tmp_path / "big.txt").write_text("this content is definitely longer than sixteen bytes")

    result = transfer.read_file_from_disk("big.txt")

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "ValueError"
    assert "too large" in result["results"]["error"]


def test_read_allows_files_within_size_cap(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_MAX_BYTES", "1024")

    (tmp_path / "small.txt").write_text("ok")

    result = transfer.read_file_from_disk("small.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["content"] == "ok"


def test_read_truncates_long_text_file_to_head_and_tail(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "2")

    lines = [f"line {i:03d}\n" for i in range(20)]
    (tmp_path / "long.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("long.txt")

    assert result["meta_data"]["is_error"] is False
    assert "content" not in result["results"]
    content = result["results"]["content_preview"]
    assert "line 000\n" in content
    assert "line 001\n" in content
    assert "line 018\n" in content
    assert "line 019\n" in content
    assert "line 002\n" not in content
    assert "line 017\n" not in content
    assert result["results"]["truncated"] is True
    assert result["results"]["total_lines"] == 20
    assert result["results"]["omitted_lines"] == 16
    assert result["results"]["preview_lines"] == 2


def test_read_short_file_returned_in_full(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "5")

    text = "\n".join(f"line {i}" for i in range(10)) + "\n"
    (tmp_path / "short.txt").write_text(text)

    result = transfer.read_file_from_disk("short.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["content"] == text
    assert "content_preview" not in result["results"]
    assert result["results"]["truncated"] is False
    assert result["results"]["omitted_lines"] == 0


def test_read_full_file_always_in_artifact(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "1")

    lines = [f"line {i}\n" for i in range(10)]
    (tmp_path / "full.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("full.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert "content" not in result["results"]
    assert "content_preview" in result["results"]
    assert base64.b64decode(result["artifacts"][0]["b64"]) == "".join(lines).encode("utf-8")
    assert result["artifacts"][0]["size"] == len("".join(lines).encode("utf-8"))


def test_read_binary_file_has_no_text_in_result(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    payload = b"\x00\x01\x02binary\xff\xfe"
    (tmp_path / "blob.bin").write_bytes(payload)

    result = transfer.read_file_from_disk("blob.bin")

    assert result["meta_data"]["is_error"] is False
    assert "content" not in result["results"]
    assert "content_preview" not in result["results"]
    assert "content_base64" not in result["results"]
    assert "truncated" not in result["results"]
    assert base64.b64decode(result["artifacts"][0]["b64"]) == payload


def test_read_preview_lines_env_var_controls_budget(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "3")

    lines = [f"row {i}\n" for i in range(30)]
    (tmp_path / "data.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("data.txt")

    assert result["meta_data"]["is_error"] is False
    content = result["results"]["content_preview"]
    assert "row 0\n" in content
    assert "row 1\n" in content
    assert "row 2\n" in content
    assert "row 27\n" in content
    assert "row 28\n" in content
    assert "row 29\n" in content
    assert "row 3\n" not in content
    assert "row 26\n" not in content
    assert result["results"]["truncated"] is True
    assert result["results"]["omitted_lines"] == 24
    assert result["results"]["total_lines"] == 30


def test_preview_lines_helper_fallbacks(monkeypatch):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.delenv("MCP_TRANSFER_PREVIEW_LINES", raising=False)
    assert transfer._preview_lines() == transfer.DEFAULT_PREVIEW_LINES
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "")
    assert transfer._preview_lines() == transfer.DEFAULT_PREVIEW_LINES
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "abc")
    assert transfer._preview_lines() == transfer.DEFAULT_PREVIEW_LINES
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "0")
    assert transfer._preview_lines() == transfer.DEFAULT_PREVIEW_LINES
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "-5")
    assert transfer._preview_lines() == transfer.DEFAULT_PREVIEW_LINES
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "7")
    assert transfer._preview_lines() == 7


def test_read_default_budget_of_50_truncates(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("MCP_TRANSFER_PREVIEW_LINES", raising=False)

    lines = [f"line {i}\n" for i in range(200)]
    (tmp_path / "big.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("big.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert result["results"]["preview_lines"] == 50
    assert result["results"]["total_lines"] == 200
    assert result["results"]["omitted_lines"] == 100
    content = result["results"]["content_preview"]
    assert "line 0\n" in content
    assert "line 49\n" in content
    assert "line 50\n" not in content
    assert "line 199\n" in content
    assert "line 150\n" in content
    assert "line 149\n" not in content


def test_read_boundary_2n_plus_1_truncates(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("MCP_TRANSFER_PREVIEW_LINES", raising=False)

    n = transfer.DEFAULT_PREVIEW_LINES
    lines = [f"row {i}\n" for i in range(2 * n + 1)]
    (tmp_path / "boundary.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("boundary.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert result["results"]["total_lines"] == 2 * n + 1
    assert result["results"]["omitted_lines"] == 1
    assert "1 line omitted" in result["results"]["content_preview"]


def test_read_boundary_2n_not_truncated(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("MCP_TRANSFER_PREVIEW_LINES", raising=False)

    n = transfer.DEFAULT_PREVIEW_LINES
    text = "".join(f"row {i}\n" for i in range(2 * n))
    (tmp_path / "exact.txt").write_text(text)

    result = transfer.read_file_from_disk("exact.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is False
    assert result["results"]["content"] == text
    assert "content_preview" not in result["results"]


def test_read_byte_cap_trims_single_long_line(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_BYTES", "200")

    payload = "X" * 4000 + "\n"
    (tmp_path / "minified.txt").write_text(payload)

    result = transfer.read_file_from_disk("minified.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert result["results"]["total_lines"] == 1
    assert result["results"]["omitted_lines"] == 0
    assert "content" not in result["results"]
    preview = result["results"]["content_preview"]
    assert "preview trimmed to 200 bytes" in preview
    assert len(preview.encode("utf-8")) <= 200
    assert base64.b64decode(result["artifacts"][0]["b64"]) == payload.encode("utf-8")


def test_read_byte_cap_measures_encoded_bytes_not_code_points(monkeypatch, tmp_path):
    """The byte ceiling is measured on encoded bytes, so CJK content (3 bytes
    per char in UTF-8) is capped correctly, not by code-point count."""
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_BYTES", "300")
    # One line of CJK chars: each char is 3 bytes in UTF-8.
    # 200 chars = 600 bytes, well over the 300-byte ceiling but only 1 line.
    payload = "\u4e00" * 200 + "\n"
    (tmp_path / "cjk.txt").write_text(payload, encoding="utf-8")

    result = transfer.read_file_from_disk("cjk.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert result["results"]["total_lines"] == 1
    assert result["results"]["omitted_lines"] == 0
    assert "content" not in result["results"]
    preview = result["results"]["content_preview"]
    # The preview must be under the byte ceiling (not the code-point count).
    assert len(preview.encode("utf-8")) <= 300
    assert "preview trimmed to 300 bytes" in preview
    # Full file still in artifact.
    assert base64.b64decode(result["artifacts"][0]["b64"]) == payload.encode("utf-8")


def test_read_combined_line_and_byte_truncation_recomputes_omitted(monkeypatch, tmp_path):
    """When both line and byte truncation apply, omitted_lines is recomputed
    from the lines actually shown after the byte trim."""
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_LINES", "50")
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_BYTES", "200")

    # 200 lines, each 80 chars: 16000 bytes total. Line budget would show
    # 100 lines (50 head + 50 tail), but the 200-byte ceiling trims to ~2
    # lines per side.
    lines = [f"line {i:03d} " + "X" * 70 + "\n" for i in range(200)]
    (tmp_path / "combined.txt").write_text("".join(lines))

    result = transfer.read_file_from_disk("combined.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    assert result["results"]["total_lines"] == 200
    # omitted_lines must reflect lines actually omitted (not just the line
    # budget gap), so total_lines - omitted_lines == lines shown.
    omitted = result["results"]["omitted_lines"]
    shown = result["results"]["total_lines"] - omitted
    assert shown < 100  # byte trim cut well below the 100-line line budget
    # The preview text is under the byte ceiling.
    preview = result["results"]["content_preview"]
    assert len(preview.encode("utf-8")) <= 200


def test_read_very_small_byte_ceiling_does_not_exceed_budget(monkeypatch, tmp_path):
    """A very small preview_bytes (below the marker size) still produces a
    result that does not exceed the byte ceiling."""
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_PREVIEW_BYTES", "50")

    (tmp_path / "tiny.txt").write_text("A" * 500 + "\n")

    result = transfer.read_file_from_disk("tiny.txt")

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["truncated"] is True
    preview = result["results"]["content_preview"]
    assert len(preview.encode("utf-8")) <= 50


class _FakeResponse:
    """Minimal stand-in for a streamed ``requests`` response."""

    def __init__(self, body: bytes, content_length=None):
        self._body = body
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        return None


def _stub_requests(monkeypatch, transfer, body: bytes, content_length=None, capture=None):
    def fake_get(url, timeout=30, stream=True):
        if capture is not None:
            capture["url"] = url
        return _FakeResponse(body, content_length=content_length)

    monkeypatch.setattr(transfer.requests, "get", fake_get)


def test_write_session_file_fetches_from_backend(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("BACKEND_URL", "http://localhost:8000")

    capture = {}
    payload = b"ISO-10303-21;\nSTEP DATA\nEND-ISO-10303-21;"
    _stub_requests(monkeypatch, transfer, payload, capture=capture)

    result = transfer.write_file_to_disk(
        "exports/part.step",
        filename="/mcp/files/download/abc123?token=xyz",
    )

    assert result["meta_data"]["is_error"] is False
    assert result["results"]["size_bytes"] == len(payload)
    assert (tmp_path / "exports" / "part.step").read_bytes() == payload
    # The relative backend path is resolved against BACKEND_URL before fetching.
    assert capture["url"] == "http://localhost:8000/mcp/files/download/abc123?token=xyz"


def test_write_session_file_to_directory_uses_original_filename(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    dest_dir = tmp_path / "tests4"
    dest_dir.mkdir()
    payload = b"binary-step-bytes"
    _stub_requests(monkeypatch, transfer, payload)

    result = transfer.write_file_to_disk(
        str(dest_dir),
        filename="/mcp/files/download/key?token=t",
        original_filename="solidworks_export_20260621_194547.step",
    )

    assert result["meta_data"]["is_error"] is False
    written = dest_dir / "solidworks_export_20260621_194547.step"
    assert written.read_bytes() == payload
    assert result["results"]["path"].endswith("solidworks_export_20260621_194547.step")


def test_write_session_file_rejected_when_over_size_cap(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_TRANSFER_MAX_BYTES", "8")

    # Declared Content-Length over the cap should fail before buffering.
    _stub_requests(monkeypatch, transfer, b"x" * 64, content_length=64)

    result = transfer.write_file_to_disk(
        "big.bin",
        filename="/mcp/files/download/key?token=t",
    )

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "ValueError"
    assert "too large" in result["results"]["error"]
    assert not (tmp_path / "big.bin").exists()


def test_write_requires_content_or_filename(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    result = transfer.write_file_to_disk("empty.txt")

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "ValueError"
    assert "Nothing to write" in result["results"]["error"]


def test_write_unknown_filename_is_rejected(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    # A bare name that the backend never rewrote to a URL is not a session file.
    result = transfer.write_file_to_disk(
        "out.step",
        filename="solidworks_export_20260621_194547.step",
    )

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "ValueError"
    assert "not a known session file" in result["results"]["error"]
