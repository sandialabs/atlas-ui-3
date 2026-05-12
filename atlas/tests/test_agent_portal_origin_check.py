"""Origin allowlist tests for the agent_portal WebSocket stream.

The stream endpoint at /api/agent-portal/processes/{id}/stream rejects
WebSocket upgrades whose Origin header is not loopback or in the
AGENT_PORTAL_ALLOWED_ORIGINS env-driven allowlist. These tests exercise
``_origin_is_allowed`` directly because the behaviour is a security gate
and the function has two distinct paths (loopback short-circuit and the
env-list lookup) that benefit from focused coverage.
"""

from __future__ import annotations

from typing import Iterator

import pytest

from atlas.routes import agent_portal_routes as ap_routes


class _Settings:
    agent_portal_allowed_origins: str = ""


class _CM:
    def __init__(self, allowed: str = "") -> None:
        self.app_settings = _Settings()
        self.app_settings.agent_portal_allowed_origins = allowed


@pytest.fixture
def patch_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[callable]:
    def _apply(allowed: str = "") -> None:
        monkeypatch.setattr(
            ap_routes.app_factory, "get_config_manager", lambda: _CM(allowed)
        )

    yield _apply


def test_origin_missing_is_rejected(patch_settings):
    patch_settings("")
    assert ap_routes._origin_is_allowed(None) is False
    assert ap_routes._origin_is_allowed("") is False


def test_origin_loopback_is_allowed_when_list_empty(patch_settings):
    patch_settings("")
    assert ap_routes._origin_is_allowed("http://localhost:8000") is True
    assert ap_routes._origin_is_allowed("http://127.0.0.1") is True
    assert ap_routes._origin_is_allowed("http://[::1]:5173") is True
    assert ap_routes._origin_is_allowed("https://localhost") is True


def test_non_loopback_origin_rejected_when_list_empty(patch_settings):
    patch_settings("")
    assert ap_routes._origin_is_allowed("https://attacker.example.com") is False
    assert ap_routes._origin_is_allowed("https://atlas-dev.example.com") is False


def test_listed_origin_is_allowed(patch_settings):
    patch_settings("atlas-dev.example.com")
    assert ap_routes._origin_is_allowed("https://atlas-dev.example.com") is True
    assert ap_routes._origin_is_allowed("https://atlas-dev.example.com:8443") is True


def test_unlisted_origin_still_rejected_when_list_populated(patch_settings):
    patch_settings("atlas-dev.example.com")
    assert ap_routes._origin_is_allowed("https://attacker.example.com") is False
    assert ap_routes._origin_is_allowed("https://atlas-dev.example.com.attacker.com") is False


def test_origin_allowlist_is_normalized(patch_settings):
    patch_settings("  Atlas-Dev.Example.COM ,  atlas.internal ,, ")
    assert ap_routes._origin_is_allowed("https://atlas-dev.example.com") is True
    assert ap_routes._origin_is_allowed("https://ATLAS-DEV.EXAMPLE.COM") is True
    assert ap_routes._origin_is_allowed("https://atlas.internal") is True
    assert ap_routes._origin_is_allowed("https://other.example.com") is False


def test_origin_must_be_http_or_https(patch_settings):
    patch_settings("atlas-dev.example.com")
    assert ap_routes._origin_is_allowed("file://atlas-dev.example.com") is False
    assert ap_routes._origin_is_allowed("ws://atlas-dev.example.com") is False
    assert ap_routes._origin_is_allowed("javascript:alert(1)") is False


def test_malformed_origin_is_rejected(patch_settings):
    patch_settings("atlas-dev.example.com")
    assert ap_routes._origin_is_allowed("not a url") is False
