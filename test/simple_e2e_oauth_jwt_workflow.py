#!/usr/bin/env python3
"""Request-level E2E tests for OAuth/JWT-related workflows.

Avoids Playwright/browser automation.

Coverage focus:
- JWT CRUD endpoints (user + admin)
- Backend MCP integration: after admin JWT upload, MCP tool discovery succeeds
  against mocks/mcp-http-mock (which requires Bearer token auth).

These tests are designed to run under `test/e2e_tests.sh`.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List

import requests


BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
AUTH_HEADERS = {"X-User-Email": os.environ.get("E2E_TEST_USER", "test@test.com")}
ADMIN_HEADERS = {"X-User-Email": os.environ.get("E2E_ADMIN_USER", "test@test.com")}

MCP_SERVER_NAME = os.environ.get("E2E_MCP_SERVER_NAME", "mcp-http-mock")
VALID_MCP_BEARER = os.environ.get("E2E_MCP_BEARER_TOKEN", "test-api-key-123")


def _http(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    timeout = kwargs.pop("timeout", 10)
    return requests.request(method, url, timeout=timeout, **kwargs)


def wait_for_backend_ready(max_retries: int = 30, delay: float = 1.0) -> None:
    for i in range(1, max_retries + 1):
        try:
            r = _http("GET", "/api/config", headers=AUTH_HEADERS)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        print(f"  [{i}/{max_retries}] backend not ready; sleeping {delay}s")
        time.sleep(delay)
    raise RuntimeError("Backend did not become ready")


def get_config() -> Dict[str, Any]:
    r = _http("GET", "/api/config", headers=AUTH_HEADERS)
    if r.status_code != 200:
        raise AssertionError(f"/api/config failed: {r.status_code} {r.text}")
    return r.json()


def get_tools_servers(config: Dict[str, Any]) -> List[str]:
    tools = config.get("tools", [])
    return [entry.get("server") for entry in tools if isinstance(entry, dict) and entry.get("server")]


def find_server_tools(config: Dict[str, Any], server_name: str) -> Dict[str, Any] | None:
    for entry in config.get("tools", []):
        if isinstance(entry, dict) and entry.get("server") == server_name:
            return entry
    return None


def admin_reload_mcp() -> None:
    r = _http("POST", "/admin/mcp/reload", headers=ADMIN_HEADERS)
    if r.status_code != 200:
        raise AssertionError(f"/admin/mcp/reload failed: {r.status_code} {r.text}")


def admin_delete_jwt(server_name: str) -> None:
    r = _http("DELETE", f"/admin/mcp/{server_name}/jwt", headers=ADMIN_HEADERS)
    if r.status_code not in (200, 404):
        raise AssertionError(f"DELETE /admin/mcp/{server_name}/jwt unexpected: {r.status_code} {r.text}")


def user_delete_jwt(server_name: str) -> None:
    r = _http("DELETE", f"/api/user/mcp/{server_name}/jwt", headers=AUTH_HEADERS)
    if r.status_code not in (200, 404):
        raise AssertionError(f"DELETE /api/user/mcp/{server_name}/jwt unexpected: {r.status_code} {r.text}")


def test_user_jwt_crud() -> None:
    print("Testing user JWT CRUD endpoints...")

    # Clean slate
    user_delete_jwt(MCP_SERVER_NAME)

    # Upload
    r = _http(
        "POST",
        "/api/user/mcp/jwt",
        headers=AUTH_HEADERS,
        json={"server_name": MCP_SERVER_NAME, "jwt_token": VALID_MCP_BEARER},
    )
    assert r.status_code == 200, f"user upload failed: {r.status_code} {r.text}"

    # Status
    r = _http("GET", f"/api/user/mcp/{MCP_SERVER_NAME}/jwt", headers=AUTH_HEADERS)
    assert r.status_code == 200, f"user status failed: {r.status_code} {r.text}"
    payload = r.json()
    assert payload.get("server_name") == MCP_SERVER_NAME
    assert payload.get("has_jwt") is True

    # List
    r = _http("GET", "/api/user/mcp/jwt/list", headers=AUTH_HEADERS)
    assert r.status_code == 200, f"user list failed: {r.status_code} {r.text}"
    servers = r.json().get("servers", [])
    assert MCP_SERVER_NAME in servers, f"Expected {MCP_SERVER_NAME} in user jwt list; got: {servers}"

    # Delete
    r = _http("DELETE", f"/api/user/mcp/{MCP_SERVER_NAME}/jwt", headers=AUTH_HEADERS)
    assert r.status_code == 200, f"user delete failed: {r.status_code} {r.text}"

    # Status should be false
    r = _http("GET", f"/api/user/mcp/{MCP_SERVER_NAME}/jwt", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json().get("has_jwt") is False


def test_admin_jwt_enables_mcp_discovery() -> None:
    print("Testing admin JWT upload enables MCP discovery...")

    # Ensure no admin JWT exists
    admin_delete_jwt(MCP_SERVER_NAME)

    # Reload MCP to clear any cached successful state
    admin_reload_mcp()

    cfg = get_config()
    server_entry = find_server_tools(cfg, MCP_SERVER_NAME)

    # With no JWT, the authenticated mock server should typically not show tools.
    # We tolerate absence OR zero tools (depending on how discovery failures are represented).
    if server_entry is not None:
        tool_count = int(server_entry.get("tool_count") or 0)
        assert tool_count == 0, f"Expected no tools before JWT upload; got {tool_count}"

    # Upload admin JWT (this is the token that client-side MCP discovery actually consumes today)
    r = _http(
        "POST",
        f"/admin/mcp/{MCP_SERVER_NAME}/jwt",
        headers=ADMIN_HEADERS,
        json={"jwt_token": VALID_MCP_BEARER},
    )
    assert r.status_code == 200, f"admin upload failed: {r.status_code} {r.text}"

    # Reload to force tool discovery using the stored JWT
    admin_reload_mcp()

    cfg = get_config()
    servers = get_tools_servers(cfg)
    assert MCP_SERVER_NAME in servers, f"Expected {MCP_SERVER_NAME} in /api/config tools servers: {servers}"

    server_entry = find_server_tools(cfg, MCP_SERVER_NAME)
    assert server_entry is not None
    tool_count = int(server_entry.get("tool_count") or 0)
    assert tool_count > 0, "Expected tools after admin JWT upload"

    tool_names = server_entry.get("tools", [])
    assert isinstance(tool_names, list)
    assert "select_users" in tool_names, f"Expected select_users tool; got: {tool_names}"


def run() -> None:
    print("\nStarting OAuth/JWT request-level E2E tests")
    print(f"Base URL: {BASE_URL}")
    print(f"Test user: {AUTH_HEADERS.get('X-User-Email')}")
    print(f"MCP server: {MCP_SERVER_NAME}")

    wait_for_backend_ready()

    failures: List[str] = []
    for t in (test_user_jwt_crud, test_admin_jwt_enables_mcp_discovery):
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:  # noqa: BLE001
            failures.append(f"{t.__name__}: {e}")
            print("FAIL", t.__name__, "-", e)

    if failures:
        print("\nFailures:")
        for f in failures:
            print(" -", f)
        raise SystemExit(1)

    print("\nOAuth/JWT E2E tests passed")


if __name__ == "__main__":
    run()
