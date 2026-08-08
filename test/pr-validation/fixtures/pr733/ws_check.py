#!/usr/bin/env python3
"""Drive chat turns over the WebSocket and check how a tool rejection is reported.

Exits non-zero with a description of the first failed expectation.

Usage: ws_check.py <backend_port>
"""

import asyncio
import json
import sys

import websockets


async def run_turn(port: int, selected_tools: list) -> dict:
    """Send one chat turn; return the error frame if any, plus streamed text."""
    result = {"error_frame": None, "text": ""}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "content": "Look up the safety document titled 'Lockout Tagout'.",
            "model": "strict-stub",
            "selected_tools": selected_tools,
            "selected_prompts": [],
            "selected_data_sources": [],
            "user": "test@test.com",
            "files": {},
            "agent_mode": False,
            "temperature": 0.7,
            "save_mode": "none",
            "incognito": True,
        }))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            data = json.loads(raw)
            kind = data.get("type")
            if kind == "token_stream":
                result["text"] += data.get("token", "")
            elif kind == "error":
                result["error_frame"] = data
            elif kind in ("response_complete", "chat_response"):
                return result


async def main() -> int:
    port = int(sys.argv[1])
    failures = []

    control = await run_turn(port, ["badschema_healthy"])
    if control["error_frame"] is not None:
        failures.append(
            f"control turn with only valid schemas errored: {control['error_frame']}"
        )
    if "accepted" not in control["text"]:
        failures.append(f"control turn did not stream the stub reply: {control['text']!r}")

    repro = await run_turn(port, ["badschema_lookup", "badschema_healthy"])
    frame = repro["error_frame"]
    if frame is None:
        failures.append("turn with an invalid tool schema produced no error frame")
    else:
        message = frame.get("message", "")
        if "badschema_lookup" not in message:
            failures.append(f"error message does not name the rejected tool: {message!r}")
        if "badschema_healthy" in message:
            failures.append(f"error message names a tool the provider did not reject: {message!r}")
        if frame.get("error_type") != "bad_request":
            failures.append(f"error_type was {frame.get('error_type')!r}, expected 'bad_request'")

    for failure in failures:
        print(f"  {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
