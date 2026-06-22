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
