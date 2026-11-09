"""Scripted OpenAI-compatible mock LLM for PR #1141 (subagent tool rename).

The model is a puppet: a directive in the last user message spells out the
exact tool calls to emit, by *name*, so the scenario can call the subagent
tools under their new names (``atlas_launch_subagent``) and under their
deprecated ones (``atlas_launch``) and compare what the backend does.

Directives (whitespace separated, key=value) in the last user message:

  plan=A+B,C   rounds separated by ``,``; calls within one round by ``+``.
               Each item is the literal tool name to call.
  ws=NAME      workspace passed to a launch call (default "Research")
  model=NAME   model passed to a launch call (default "scripted-mock")

Arguments are chosen from the tool's *role*, which both spellings share:
a discovery or list call takes none, a launch takes workspace/model/prompt, and
a result call takes the ``run_id`` found in the earlier tool results.

After the plan the mock answers ``TOOLSAID<<<...>>>`` with the tool messages it
was handed. ``GET /test/log`` returns the tools each request was offered.
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


def parse_directives(text):
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


def arguments_for(name, d, tool_messages):
    if "discover" in name or name.endswith("runs"):
        return {}
    if name.endswith("result"):
        found = re.findall(r'"run_id":\s*"([^"]+)"', "\n".join(tool_messages))
        return {"run_id": found[0] if found else "missing"}
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
        LOG.append({"user": user_text[:200], "tools": offered})

    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    model = body.get("model", "scripted-mock")
    plan = [r.split("+") for r in d["plan"].split(",")] if d.get("plan") else None
    round_index = rounds_since_last_user(messages)
    tool_messages = tool_messages_since_last_user(messages)

    if plan is not None and round_index < len(plan):
        calls = [
            {
                "id": "call_" + uuid.uuid4().hex[:8],
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments_for(name, d, tool_messages)),
                },
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
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    if plan is not None:
        text = "TOOLSAID<<<" + "\n---\n".join(tool_messages) + ">>>"
    else:
        text = "ECHO: " + user_text[:200]
    if stream:
        def gen_text():
            yield chunk(cid, model, [{
                "index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None,
            }])
            yield chunk(cid, model, [{"index": 0, "delta": {}, "finish_reason": "stop"}])
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen_text(), media_type="text/event-stream")
    return JSONResponse({
        "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MOCK_LLM_PORT"]), log_level="warning")
