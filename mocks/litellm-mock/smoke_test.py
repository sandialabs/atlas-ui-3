#!/usr/bin/env python3
"""Smoke test for a running mock LiteLLM proxy (standard library only).

    python mocks/litellm-mock/main.py &
    python mocks/litellm-mock/smoke_test.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("MOCK_LITELLM_URL", "http://127.0.0.1:4010")
KEY = os.environ.get("MOCK_LITELLM_MASTER_KEY", "sk-mock-litellm-master")


def call(method, path, body=None, headers=None):
    request = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def check(label, condition):
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def main() -> int:
    ok = True
    status, teams = call("GET", "/team/list?user_id=test@test.com")
    ok &= check("team list for test@test.com", status == 200 and [t["team_alias"] for t in teams] == ["Project Alpha", "Project Beta"])

    status, models = call("GET", "/models?team_id=team-alpha-7f3a")
    ok &= check("models for Project Alpha", status == 200 and [m["id"] for m in models["data"]] == ["gpt-4o-mini", "claude-sonnet"])

    chat = {"model": "llama-3.3-70b", "messages": [{"role": "user", "content": "hi"}]}
    status, _ = call("POST", "/chat/completions", chat)
    ok &= check("chat without team header is refused", status == 400)

    status, _ = call("POST", "/chat/completions", chat, {"x-litellm-team-id": "team-alpha-7f3a"})
    ok &= check("model outside the team is refused", status == 401)

    status, reply = call("POST", "/chat/completions", chat, {"x-litellm-team-id": "team-beta-19c2"})
    ok &= check(
        "chat with team header succeeds",
        status == 200 and reply["choices"][0]["message"]["content"].startswith("[Project Beta / llama-3.3-70b]"),
    )

    status, log = call("GET", "/mock/requests")
    ok &= check("request log records the team header", status == 200 and log["requests"][-1]["team_header"] == "team-beta-19c2")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
