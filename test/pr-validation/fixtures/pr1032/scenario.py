"""End-to-end scenario for PR #1032 against a running backend.

Issue #1032: models, MCP servers and RAG sources declare explicit
``allowed_data_classifications``; the active conversation classification must
be a member of each, enforced on the server for the whole turn. Drives the
real REST API and WebSocket endpoint. Prints one PASSED/FAILED line per check
and exits non-zero if any failed.
"""
import asyncio
import json
import os
import sys
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
USER = "test@test.com"
FAILED = 0


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def get(path):
    req = urllib.request.Request(API + path, headers={"X-User-Email": USER})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


async def turn(model, level, tools=None):
    """Send one chat turn; return (refusal message or None, answer text)."""
    async with websockets.connect(WS, additional_headers={"X-User-Email": USER}) as ws:
        frame = {"type": "chat", "content": "hello", "model": model}
        if level:
            frame["compliance_level_filter"] = level
        if tools:
            frame["selected_tools"] = tools
        await ws.send(json.dumps(frame))
        text = ""
        while True:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 20))
            except asyncio.TimeoutError:
                return None, text
            blob = json.dumps(m)
            if "Not approved for" in blob:
                return blob, text
            if m.get("type") == "token_stream":
                text += m.get("token") or ""
                if m.get("is_last"):
                    return None, text
            if m.get("type") in ("chat_response", "response_complete"):
                return None, text or blob
            if m.get("type") == "error":
                return None, blob


async def main():
    config = get("/api/config")
    models = {m["name"]: m for m in config.get("models", [])}
    check(models.get("model-x", {}).get("allowed_data_classifications") == ["UUR", "ITAR", "ECI"],
          "/api/config exposes a model's explicit classifications")
    check(models.get("legacy-itar", {}).get("allowed_data_classifications") == ["ITAR"],
          "/api/config reads a legacy compliance_level as a one-element list")
    check("allowed_data_classifications" not in models.get("undeclared", {}),
          "an undeclared model carries no classifications")

    # The issue's acceptance table, over the real WebSocket.
    for level, model, allowed in [
        ("UUR", "model-y", True),
        ("ITAR", "model-y", False),
        ("ECI", "model-y", False),
        ("ITAR", "model-x", True),
        ("ECI", "model-x", True),
        ("ITAR", "legacy-itar", True),
        ("UUR", "legacy-itar", False),
        ("UUR", "undeclared", False),
    ]:
        refusal, answer = await turn(model, level)
        if allowed:
            check(refusal is None and "ECHO" in answer, f"{model} runs in a {level} conversation")
        else:
            check(refusal is not None and "the selected model" in refusal,
                  f"{model} is refused in a {level} conversation")

    refusal, _ = await turn("model-x", "ITAR", ["google_search_search"])
    check(refusal is not None and "tool server google_search" in refusal,
          "a UUR-only MCP server is refused in an ITAR conversation")
    refusal, _ = await turn("model-x", "ECI", ["bare_server_tool"])
    check(refusal is not None and "tool server bare_server" in refusal,
          "an MCP server with no declaration fails closed")
    refusal, _ = await turn("model-x", "ITAR", ["internal_search_search"])
    check(refusal is None, "a multi-classification MCP server is allowed in ITAR")
    refusal, _ = await turn("model-x", "UUR", ["google_search_search"])
    check(refusal is None, "the UUR-only MCP server is allowed in a UUR conversation")
    refusal, answer = await turn("undeclared", None)
    check(refusal is None and "ECHO" in answer, "no level selected: nothing is refused")

    sys.exit(1 if FAILED else 0)


asyncio.run(main())
