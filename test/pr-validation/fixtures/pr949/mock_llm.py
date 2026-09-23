"""Scripted OpenAI-compatible mock LLM for PR #949 launch-discovery testing.

The model is a puppet: a directive in the last user message names the exact
sequence of tool calls it should emit, one per round, so the scenario can drive
the *order* of ``atlas_discover_launch_options`` and ``atlas_launch`` -- which
is the whole point of the feature under test -- rather than hoping a real model
happens to call them the right way round.

Directives (whitespace separated, key=value) in the last user message:

  plan=NAME    which scripted sequence to run (default ``echo``)
  ws=NAME      workspace name passed to atlas_launch (default "Research")
  model=NAME   model name passed to atlas_launch (default "scripted-mock")

Plans:

  launch_only        atlas_launch, with no discovery first. The launch must be
                     refused by the backend; the mock then answers with the
                     refusal text it was handed, so the scenario can assert on
                     what the model was actually told.
  discover_launch    atlas_discover_launch_options, then atlas_launch.
  same_step          both calls in ONE assistant turn, atlas_launch listed
                     first. Exercises the discovery-first pre-pass in
                     ``execute_multiple_tools``.
  discover_only      atlas_discover_launch_options alone, then answer with the
                     raw options payload so the scenario can inspect its shape.

Without a directive the mock answers immediately with "ECHO: <content>".
"""
import json
import os
import re
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
LOG = []
LOCK = threading.Lock()

DISCOVER = "atlas_discover_launch_options"
LAUNCH = "atlas_launch"

# Each plan is the list of tool-call rounds to emit, in order. A round is a
# list so a single assistant turn can carry more than one call.
PLANS = {
    "launch_only": [[LAUNCH]],
    "discover_launch": [[DISCOVER], [LAUNCH]],
    "same_step": [[LAUNCH, DISCOVER]],
    "discover_only": [[DISCOVER]],
}


def parse_directives(text):
    # Plain split rather than a regex: the prompt is user input and CodeQL
    # flags adjacent overlapping classes as a polynomial match.
    d = {}
    for token in (text or "").split():
        key, sep, value = token.partition("=")
        if sep and key.isidentifier() and value:
            d[key] = value
    return d


def last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return c or ""
    return ""


def rounds_since_last_user(messages):
    """How many assistant tool-call turns have already happened this turn."""
    n = 0
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n += 1
    return n


def tool_messages_since_last_user(messages):
    out = []
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        if m.get("role") == "tool":
            out.append(m.get("content") or "")
    return list(reversed(out))


def arguments_for(name, d):
    if name == DISCOVER:
        return {}
    return {
        "workspace": d.get("ws", "Research"),
        "model": d.get("model", "scripted-mock"),
        "prompt": "summarize the corpus",
    }


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


def chunk(cid, model, choices):
    return sse({
        "id": cid, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model, "choices": choices,
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test/log")
async def get_log():
    return LOG


@app.post("/test/reset")
async def reset():
    LOG.clear()
    return {"ok": True}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "scripted-mock", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    stream = bool(body.get("stream"))
    user_text = last_user(messages)
    d = parse_directives(user_text)
    offered = [t.get("function", {}).get("name") for t in tools]
    with LOCK:
        LOG.append({
            "t": time.time(), "user": user_text[:200], "n_msgs": len(messages),
            "tools": offered, "stream": stream,
        })

    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    model = body.get("model", "scripted-mock")
    plan = PLANS.get(d.get("plan", ""), None)
    round_index = rounds_since_last_user(messages)

    if plan is not None and round_index < len(plan):
        calls = [
            {
                "id": "call_" + uuid.uuid4().hex[:8],
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments_for(name, d))},
            }
            for name in plan[round_index]
        ]
        if stream:
            def gen():
                for i, call in enumerate(calls):
                    yield chunk(cid, model, [{
                        "index": 0,
                        "delta": {
                            "role": "assistant", "content": None,
                            "tool_calls": [{
                                "index": i, "id": call["id"], "type": "function",
                                "function": {"name": call["function"]["name"], "arguments": ""},
                            }],
                        },
                        "finish_reason": None,
                    }])
                    yield chunk(cid, model, [{
                        "index": 0,
                        "delta": {"tool_calls": [{
                            "index": i,
                            "function": {"arguments": call["function"]["arguments"]},
                        }]},
                        "finish_reason": None,
                    }])
                yield chunk(cid, model, [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}])
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": calls},
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        })

    if plan is not None:
        # The turn is over. Echo back verbatim what the tools told this model,
        # so the scenario asserts on what the backend actually said rather than
        # on the mock's own wording.
        seen = tool_messages_since_last_user(messages)
        text = "TOOLSAID<<<" + " ||| ".join(seen) + ">>>"
    else:
        text = "ECHO: " + user_text

    if stream:
        def gen2():
            yield chunk(cid, model, [{
                "index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None,
            }])
            for tok in re.findall(r"\S+\s*", text):
                yield chunk(cid, model, [{
                    "index": 0, "delta": {"content": tok}, "finish_reason": None,
                }])
            yield chunk(cid, model, [{"index": 0, "delta": {}, "finish_reason": "stop"}])
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen2(), media_type="text/event-stream")
    return JSONResponse({
        "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    })


if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1",
        port=int(os.environ.get("MOCK_LLM_PORT", "8042")),
        log_level="warning",
    )
