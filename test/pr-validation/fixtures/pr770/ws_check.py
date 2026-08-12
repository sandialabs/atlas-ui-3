#!/usr/bin/env python3
"""Drive a RAG + tools chat turn and check the message list the provider receives.

The regression: on the continuation round the retrieved-context system message
was inserted before the last message, landing between the assistant `tool_calls`
message and its tool reply. The stub provider rejects that exactly as
Azure/OpenAI does, and the rejection used to surface as a crash
("'NoneType' object is not iterable") rather than an answer.

Exits non-zero with a description of the first failed expectation.

Usage: ws_check.py <backend_port> <stub_port>
"""

import asyncio
import json
import sys
import urllib.request

import websockets


async def run_turn(port: int, data_sources: list) -> dict:
    result = {"error_frame": None, "text": "", "final": ""}
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "content": "Look up the document titled 'Lockout Tagout' and summarize it.",
            "model": "order-stub",
            "selected_tools": ["orderdocs_lookup"],
            "selected_prompts": [],
            "selected_data_sources": data_sources,
            "user": "test@test.com",
            "files": {},
            "agent_mode": False,
            "temperature": 0.7,
            "save_mode": "none",
            "incognito": True,
        }))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            data = json.loads(raw)
            kind = data.get("type")
            if kind == "tool_approval_request":
                # The server always asks; the UI answers (auto-approve or the
                # review row). Approve so the tool round completes.
                await ws.send(json.dumps({
                    "type": "tool_approval_response",
                    "tool_call_id": data.get("tool_call_id"),
                    "approved": True,
                    "arguments": data.get("arguments"),
                }))
            elif kind == "token_stream":
                result["text"] += data.get("token", "")
            elif kind == "error":
                result["error_frame"] = data
            elif kind == "chat_response":
                result["final"] = data.get("message", "")
            elif kind == "response_complete":
                return result


def stub_requests(stub_port: int) -> list:
    with urllib.request.urlopen(f"http://127.0.0.1:{stub_port}/_requests") as resp:
        return json.load(resp)["requests"]


async def main() -> int:
    backend_port = int(sys.argv[1])
    stub_port = int(sys.argv[2])
    failures = []

    turn = await run_turn(backend_port, ["atlas_rag:technical-docs"])

    if turn["error_frame"] is not None:
        failures.append(f"RAG+tools turn errored: {turn['error_frame']}")

    answer = turn["text"] + turn["final"]
    if "NoneType" in answer:
        failures.append(f"turn surfaced the NoneType crash: {answer!r}")
    if "retrieved and summarized" not in answer:
        failures.append(f"turn did not produce the stub's final answer: {answer!r}")

    # The stub logs the roles of every request it received, tagging the
    # retrieved-context message as `system:rag`. The continuation round must
    # show the assistant tool_calls message immediately followed by its tool
    # reply, with the RAG context ahead of the user turn.
    requests = stub_requests(stub_port)
    if len(requests) < 2:
        failures.append(f"expected a continuation round, saw {len(requests)} provider call(s)")
    else:
        continuation = requests[-1]
        if "system:rag" not in continuation:
            failures.append(f"RAG context message missing from the request: {continuation}")
        if "tool" not in continuation or "assistant" not in continuation:
            failures.append(f"continuation round carried no tool round: {continuation}")
        else:
            idx = continuation.index("assistant")
            if continuation[idx + 1] != "tool":
                failures.append(
                    f"a {continuation[idx + 1]!r} message split the assistant/tool block: {continuation}"
                )
            if "system:rag" in continuation and continuation.index("system:rag") > idx:
                failures.append(
                    f"RAG context landed after the assistant tool_calls message: {continuation}"
                )

    for failure in failures:
        print(f"  {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
