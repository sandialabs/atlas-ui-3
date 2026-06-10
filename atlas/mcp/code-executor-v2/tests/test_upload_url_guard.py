"""SSRF / scheme guard for ``upload_file(file_url=...)``.

These tests exercise ``_resolve_upload_url`` directly. They monkey-patch
``socket.getaddrinfo`` so DNS lookups are deterministic and isolated.
"""
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def _kernel_override(monkeypatch, tmp_path):
    # Loading main.py would run the kernel probe and exit on hostile hosts;
    # set the override BEFORE the import in this fixture so every test in
    # this module imports a working main module.
    monkeypatch.setenv("CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX", "1")
    monkeypatch.setenv("CHATUI_BACKEND_BASE_URL", "http://backend.example:8000")
    # Avoid the production /workspaces default, which is not writable in
    # CI / in a developer's home dir.
    monkeypatch.setenv("CODE_EXECUTOR_V2_WORKSPACES_DIR", str(tmp_path / "ws"))


@pytest.fixture
def main_module(monkeypatch):
    # Re-import so config picks up the patched env. We import here, after
    # the env-patching fixture, to ensure CONFIG and module globals see
    # the test's environment.
    import importlib
    import sys
    sys.modules.pop("main", None)
    import main as m
    importlib.reload(m)
    return m


def _stub_addrinfo(monkeypatch, ip: str):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_rejects_file_scheme(main_module):
    with pytest.raises(ValueError, match="unsupported url scheme"):
        main_module._resolve_upload_url("file:///etc/passwd")


def test_rejects_data_scheme(main_module):
    with pytest.raises(ValueError, match="unsupported url scheme"):
        main_module._resolve_upload_url("data:text/plain;base64,QQ==")


def test_rejects_ftp_scheme(main_module):
    with pytest.raises(ValueError, match="unsupported url scheme"):
        main_module._resolve_upload_url("ftp://example.com/x")


def test_rejects_unlisted_host(main_module, monkeypatch):
    _stub_addrinfo(monkeypatch, "8.8.8.8")
    with pytest.raises(ValueError, match="not in upload allow-list"):
        main_module._resolve_upload_url("https://evil.example/x")


def test_accepts_backend_host_even_if_internal(main_module, monkeypatch):
    # The configured backend host is allowed to resolve to a private IP —
    # that's the whole point (in-cluster service-to-service traffic).
    _stub_addrinfo(monkeypatch, "10.0.0.5")
    out = main_module._resolve_upload_url("/api/files/123/download")
    assert out == "http://backend.example:8000/api/files/123/download"


def test_blocks_loopback_for_extra_host(main_module, monkeypatch):
    monkeypatch.setenv("CODE_EXECUTOR_V2_UPLOAD_ALLOWED_HOSTS", "files.public.example")
    _stub_addrinfo(monkeypatch, "127.0.0.1")
    with pytest.raises(ValueError, match="non-public address"):
        main_module._resolve_upload_url("https://files.public.example/x")


def test_accepts_public_for_extra_host(main_module, monkeypatch):
    monkeypatch.setenv("CODE_EXECUTOR_V2_UPLOAD_ALLOWED_HOSTS", "files.public.example")
    # 1.1.1.1 (Cloudflare) — globally routable, not in any reserved range.
    _stub_addrinfo(monkeypatch, "1.1.1.1")
    out = main_module._resolve_upload_url("https://files.public.example/x.csv")
    assert out == "https://files.public.example/x.csv"


def test_relative_path_uses_backend_base(main_module, monkeypatch):
    _stub_addrinfo(monkeypatch, "10.0.0.5")
    out = main_module._resolve_upload_url("/api/files/abc")
    assert out == "http://backend.example:8000/api/files/abc"


def test_empty_url_rejected(main_module):
    with pytest.raises(ValueError, match="file_url is empty"):
        main_module._resolve_upload_url("")
