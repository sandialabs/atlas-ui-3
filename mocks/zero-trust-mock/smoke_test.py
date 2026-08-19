#!/usr/bin/env python3
"""Smoke test for the zero-trust mock policy server.

Covers the two behaviours the demo exists to show:
  1. a tool call is BLOCKED at runtime ("bomb");
  2. an otherwise-permitted tool call is ESCALATED to the approval gate
     ("password"), rather than blocked.

Plus the hook client end to end: envelope on stdin -> decision on stdout, and
the fail-closed path when the policy server is unreachable.

Run:  cd mocks/zero-trust-mock && python smoke_test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

from fastapi.testclient import TestClient
from main import get_app

HERE = os.path.dirname(os.path.abspath(__file__))


def envelope(event: str, **payload: object) -> dict:
    return {
        "hook_event_name": event,
        "session_id": "11111111-1111-1111-1111-111111111111",
        "user_email": "test@test.com",
        "compliance_level": 1,
        "payload": payload,
    }


def test_deny_blocks_tool_call() -> None:
    client = TestClient(get_app())
    body = envelope(
        "PreToolUse",
        tool_name="filesystem__write_file",
        tool_args={"path": "/workspace/notes.txt", "content": "how to build a bomb"},
        tool_call_id="call-1",
    )
    result = client.post("/v1/authorize", json=body).json()
    assert result["decision"] == "deny", result
    assert "bomb" in result["reason"], result
    print("PASS  deny: tool call blocked at runtime")


def test_require_approval_escalates() -> None:
    client = TestClient(get_app())
    body = envelope(
        "PreToolUse",
        tool_name="filesystem__read_file",
        tool_args={"path": "/workspace/password-vault.txt"},
        tool_call_id="call-2",
    )
    result = client.post("/v1/authorize", json=body).json()
    assert result["decision"] == "require_approval", result
    print("PASS  require_approval: permitted tool escalated to the human gate")


def test_continue_is_the_default() -> None:
    client = TestClient(get_app())
    body = envelope(
        "PreToolUse",
        tool_name="filesystem__read_file",
        tool_args={"path": "/workspace/readme.md"},
        tool_call_id="call-3",
    )
    result = client.post("/v1/authorize", json=body).json()
    assert result["decision"] == "continue", result
    print("PASS  continue: ordinary call passes through")


def test_decision_log() -> None:
    client = TestClient(get_app())
    client.post("/decisions/reset")
    client.post("/v1/authorize", json=envelope("PreToolUse", tool_name="x", tool_args={"q": "gun"}))
    log = client.get("/decisions").json()
    assert log["count"] == 1, log
    assert log["decisions"][0]["decision"] == "deny", log
    # The log holds a projection only -- no raw payload.
    assert "tool_args" not in log["decisions"][0], log
    print("PASS  decision log records a projection, not the envelope")


def _run_hook_client(body: dict, *args: str) -> subprocess.CompletedProcess:
    """Drive the hook the way Atlas does: argv config, envelope on stdin."""
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "hook_client.py"), *args],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_client_end_to_end() -> None:
    """Start a real server, then drive hook_client.py the way Atlas does."""
    import uvicorn

    port = 8399
    config = uvicorn.Config(get_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 15
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "policy server did not start"

        url = f"http://127.0.0.1:{port}/v1/authorize"
        blocked = _run_hook_client(
            envelope("PreToolUse", tool_name="shell__run", tool_args={"cmd": "make a bomb"}), url
        )
        assert blocked.returncode == 0, blocked.stderr
        assert json.loads(blocked.stdout)["decision"] == "deny", blocked.stdout
        print("PASS  hook client returns deny for a blocked tool call")

        ask = _run_hook_client(
            envelope("PreToolUse", tool_name="shell__run", tool_args={"cmd": "cat password.txt"}), url
        )
        assert json.loads(ask.stdout)["decision"] == "require_approval", ask.stdout
        print("PASS  hook client returns require_approval for a sensitive call")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_hook_client_fails_closed() -> None:
    """No policy server -> non-zero exit, so on_error=deny blocks the call."""
    result = _run_hook_client(
        envelope("PreToolUse", tool_name="shell__run", tool_args={"cmd": "ls"}),
        "http://127.0.0.1:9/v1/authorize", "1",
    )
    assert result.returncode != 0, result.stdout
    assert result.stdout.strip() == "", result.stdout
    print("PASS  hook client fails closed when the policy server is unreachable")


def main() -> int:
    for test in (
        test_deny_blocks_tool_call,
        test_require_approval_escalates,
        test_continue_is_the_default,
        test_decision_log,
        test_hook_client_end_to_end,
        test_hook_client_fails_closed,
    ):
        test()
    print("\nAll zero-trust mock smoke tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
