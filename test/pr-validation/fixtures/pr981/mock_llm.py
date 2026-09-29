"""Scripted OpenAI-compatible mock LLM for parallel-run testing.

Directives in the last user message (whitespace separated, key=value):
  sleep=N      seconds per atlas_sleep tool call (default 4)
  steps=K      number of atlas_sleep calls before the final answer (default 1)
  label=X      label echoed in the final answer (default "none")
  stream_ms=M  delay per streamed token in ms (default 60)
  words=W      number of words in the final answer (default 30)
Without any directive the mock answers immediately with "ECHO: <content>".
"""
import json
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

def tool_calls_since_last_user(messages):
    n = 0
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        if m.get("role") == "tool":
            n += 1
    return n

def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"

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

@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    stream = bool(body.get("stream"))
    user_text = last_user(messages)
    d = parse_directives(user_text)
    with LOCK:
        LOG.append({"t": time.time(), "user": user_text[:200], "n_msgs": len(messages),
                    "tools": [t.get("function", {}).get("name") for t in tools], "stream": stream})
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    model = body.get("model", "scripted-mock")
    tool_names = {t.get("function", {}).get("name") for t in tools}
    has_directive = bool(d)
    steps = int(d.get("steps", 1))
    done_calls = tool_calls_since_last_user(messages)
    sleep_tool = next((n for n in tool_names if n.endswith("sleep")), None)
    stream_ms = int(d.get("stream_ms", 60))
    words = int(d.get("words", 30))
    label = d.get("label", "none")

    if has_directive and sleep_tool and done_calls < steps:
        call = {"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                "function": {"name": sleep_tool, "arguments": json.dumps({"seconds": float(d.get("sleep", 4)), "reason": f"{label} step {done_calls+1}"})}}
        if stream:
            def gen():
                yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {"role": "assistant", "content": None,
                                        "tool_calls": [{"index": 0, "id": call["id"], "type": "function",
                                                        "function": {"name": call["function"]["name"], "arguments": ""}}]}, "finish_reason": None}]})
                yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": call["function"]["arguments"]}}]}, "finish_reason": None}]})
                yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse({"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                             "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    if has_directive:
        text = f"FINAL[{label}] " + " ".join(f"{label}-w{i}" for i in range(1, words + 1)) + f" END[{label}]"
    else:
        text = "ECHO: " + user_text
    if stream:
        def gen2():
            yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                       "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
            for tok in re.findall(r"\S+\s*", text):
                yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "delta": {"content": tok}, "finish_reason": None}]})
                if has_directive:
                    time.sleep(stream_ms / 1000.0)
            yield sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen2(), media_type="text/event-stream")
    return JSONResponse({"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "scripted-mock", "object": "model"}]}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(__import__("os").environ.get("MOCK_LLM_PORT", "8042")), log_level="warning")
