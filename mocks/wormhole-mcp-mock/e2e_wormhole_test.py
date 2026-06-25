#!/usr/bin/env python3
"""End-to-end validation of the Wormhole subtoken flow (issue #640).

Drives the REAL Atlas code paths against the external `wormhole-mcp-mock`
service to prove the full chain works:

    x-subtoken request header
      -> capture_subtoken_from_headers()           (what the WS/HTTP layer calls)
      -> WormholeTokenStore                         (per-user, in-memory)
      -> MCPToolManager.call_tool(...)
      -> _get_or_create_user_http_client()          (per-user/conversation)
      -> StreamableHttpTransport(headers={X-Token})  (the forward)
      -> wormhole-mcp-mock                          (external service)

Scenarios:
  A. With a captured subtoken  -> mock receives X-Token, tool authorizes.
  B. No subtoken (header absent) -> nothing forwarded, mock rejects the call.
  C. Subtoken rotation         -> a new value forwards the new X-Token (cached
                                  client rebuilt).

The mock's /log endpoint is the source of truth: it records exactly what the
external service saw, so the assertions verify the real forwarded header rather
than trusting the Atlas side.

Requires the mock to be running; set WORMHOLE_MOCK_URL (default
http://127.0.0.1:8021). Exits non-zero if any scenario fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.request

# --- Environment must be set before importing atlas (settings are cached) -----
os.environ.setdefault("FEATURE_WORMHOLE_ENABLED", "true")
os.environ.setdefault("WORMHOLE_SUBTOKEN_HEADER", "x-subtoken")
os.environ.setdefault("WORMHOLE_FORWARD_HEADER", "X-Token")
# token_storage refuses to start without a real (non-placeholder) key.
os.environ.setdefault("MCP_TOKEN_ENCRYPTION_KEY", "e2e-wormhole-test-key-not-a-placeholder-32+")

MOCK_URL = os.environ.get("WORMHOLE_MOCK_URL", "http://127.0.0.1:8021")
USER = "genesis.user@example.gov"
SUBTOKEN_1 = "wh-subtoken-AAAA-1111-2222-ZZZZ"
SUBTOKEN_2 = "wh-subtoken-BBBB-3333-4444-YYYY"


def _http_get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{MOCK_URL}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def _http_post_json(path: str, payload: dict) -> None:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{MOCK_URL}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=10).read()


def _mask(token: str) -> str:
    return f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "*" * len(token)


def _summarize_result(res: object) -> str:
    """Stringify an MCP CallToolResult for human-readable reporting."""
    data = getattr(res, "data", None)
    if data is not None:
        return str(data)[:200]
    content = getattr(res, "content", None)
    if content:
        texts = [getattr(c, "text", str(c)) for c in content]
        return " ".join(texts)[:200]
    return str(res)[:200]


async def main() -> int:
    # Imported here so the env above is applied first.
    from atlas.modules.mcp_tools.client import MCPToolManager
    from atlas.modules.mcp_tools.wormhole_token_store import (
        capture_subtoken_from_headers,
        get_wormhole_store,
    )

    # Wormhole-enabled server pointing at the mock.
    mcp_url = f"{MOCK_URL}/mcp"
    config = {
        "wormhole_demo": {
            "url": mcp_url,
            "transport": "http",
            "wormhole": True,
            "groups": ["users"],
        }
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(config, tmp)
    tmp.close()

    # Fresh mock log for a clean, screenshot-friendly run.
    _http_post_json("/reset", {})

    manager = MCPToolManager(config_path=tmp.name)
    # Initialize + discover to prove tool listing works without a subtoken.
    await manager.initialize_clients()
    await manager.discover_tools()
    discovered = sorted(manager.available_tools.keys())
    print(f"[setup] discovered tools: {discovered}")

    scenarios: list[dict] = []

    def check(name: str, passed: bool, detail: str) -> None:
        scenarios.append({"name": name, "passed": passed, "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    # --- Scenario A: subtoken captured -> forwarded as X-Token ----------------
    capture_subtoken_from_headers({"x-subtoken": SUBTOKEN_1}, USER)
    res_a = await manager.call_tool(
        "wormhole_demo", "whoami", {}, user_email=USER, conversation_id="e2e-A"
    )
    log_a = _http_get_json("/log")["requests"]
    last_a = log_a[-1] if log_a else {}
    check(
        "A. subtoken forwarded as X-Token",
        last_a.get("subtoken_present") is True
        and last_a.get("subtoken_masked") == _mask(SUBTOKEN_1),
        f"mock saw X-Token={last_a.get('subtoken_masked')} (expected {_mask(SUBTOKEN_1)}); "
        f"tool result={_summarize_result(res_a)}",
    )

    # --- Scenario B: no subtoken -> nothing forwarded, call rejected ----------
    capture_subtoken_from_headers({}, USER)  # absent header clears stored value
    assert get_wormhole_store().get_subtoken(USER) is None
    res_b = await manager.call_tool(
        "wormhole_demo", "get_protected_resource", {},
        user_email=USER, conversation_id="e2e-B",
    )
    log_b = _http_get_json("/log")["requests"]
    last_b = log_b[-1] if log_b else {}
    check(
        "B. no subtoken -> not forwarded, call rejected",
        last_b.get("subtoken_present") is False,
        f"mock saw subtoken_present={last_b.get('subtoken_present')}; "
        f"tool result={_summarize_result(res_b)}",
    )

    # --- Scenario C: rotation -> new subtoken forwarded -----------------------
    capture_subtoken_from_headers({"x-subtoken": SUBTOKEN_2}, USER)
    res_c = await manager.call_tool(
        "wormhole_demo", "whoami", {}, user_email=USER, conversation_id="e2e-A"
    )  # same conversation as A: cached client must be rebuilt for the new token
    log_c = _http_get_json("/log")["requests"]
    last_c = log_c[-1] if log_c else {}
    check(
        "C. rotated subtoken forwarded (cached client rebuilt)",
        last_c.get("subtoken_present") is True
        and last_c.get("subtoken_masked") == _mask(SUBTOKEN_2),
        f"mock saw X-Token={last_c.get('subtoken_masked')} (expected {_mask(SUBTOKEN_2)}); "
        f"tool result={_summarize_result(res_c)}",
    )

    await manager.cleanup()
    os.unlink(tmp.name)

    passed = all(s["passed"] for s in scenarios)
    report = {
        "passed": passed,
        "summary": (
            f"Atlas captured the x-subtoken request header and forwarded it as "
            f"X-Token to the external Wormhole MCP mock. {sum(s['passed'] for s in scenarios)}/"
            f"{len(scenarios)} scenarios passed."
        ),
        "scenarios": scenarios,
    }
    _http_post_json("/report", report)

    print("\n=== E2E SUMMARY ===")
    print(report["summary"])
    print("Dashboard:", f"{MOCK_URL}/status")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
