"""Tests for the local-development MCP transfer example server.

Lives under atlas/tests/ so it is collected by the CI suite
(`pytest tests` run from the atlas/ directory). The transfer example is
imported with a stubbed server factory so the FastMCP dependency is not
required to exercise the tool functions.
"""

import base64
import importlib
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


def test_path_traversal_is_denied(monkeypatch, tmp_path):
    transfer = _load_transfer_module(monkeypatch)
    monkeypatch.setenv("MCP_TRANSFER_BASE_DIR", str(tmp_path))

    result = transfer.write_file_to_disk("../outside.txt", "blocked")

    assert result["meta_data"]["is_error"] is True
    assert result["meta_data"]["error_type"] == "PermissionError"
    assert not (tmp_path.parent / "outside.txt").exists()


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
