"""Scripted OpenAI-compatible mock LLM for context compaction (issue #877).

Directives in the latest user request (whitespace separated, key=value):
  label=X        label echoed in the final answer (required for directives)
  steps=K        atlas_sleep tool calls before the final answer (default 0)
  narrate=W      words of narration on every tool-call step (default 0)
  overflow_at=N  once, after N tool calls of this label, reject the request
                 as too long for the context window (HTTP 400)
Without a directive the mock answers "ECHO: <first words>".

A request whose system prompt is the compaction summarizer's gets a fixed,
recognisable checkpoint. Tool calls are counted per label on the server, so a
compacted transcript cannot make the mock repeat a call; the scenario checks
that the backend never executes one twice either.
"""
import json
import os
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
LOG = []
ISSUED = {}
OVERFLOWED = set()
LOCK = threading.Lock()
SUMMARIZER_MARKER = "You compact a long conversation"


def parse_directives(text):
    d = {}
    for token in (text or "").split():
        key, sep, value = token.partition("=")
        if sep and key.isidentifier() and value:
            d[key] = value
    return d


def text_of(content):
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            return text_of(m.get("content"))
    return ""


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


def completion(cid, model, message, finish, prompt_chars):
    return {
        "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt_chars // 4, "completion_tokens": 20, "total_tokens": prompt_chars // 4 + 20},
    }


def stream_response(cid, model, content, tool_call, prompt_chars):
    def gen():
        base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model}
        yield sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
        if content:
            for word in content.split(" "):
                yield sse({**base, "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}]})
        if tool_call:
            yield sse({**base, "choices": [{"index": 0, "delta": {"tool_calls": [{
                "index": 0, "id": tool_call["id"], "type": "function",
                "function": {"name": tool_call["function"]["name"], "arguments": tool_call["function"]["arguments"]},
            }]}, "finish_reason": None}]})
        yield sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_call else "stop"}]})
        # Usage on a final chunk, as providers that report streaming usage do.
        yield sse({**base, "choices": [], "usage": {
            "prompt_tokens": prompt_chars // 4, "completion_tokens": 20, "total_tokens": prompt_chars // 4 + 20}})
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test/log")
async def get_log():
    return LOG


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools") or []
    stream = bool(body.get("stream"))
    model = body.get("model", "compaction-mock")
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    prompt_chars = sum(len(text_of(m.get("content"))) for m in messages)
    system = text_of(messages[0].get("content")) if messages and messages[0].get("role") == "system" else ""
    user_text = last_user(messages)
    joined = "\n".join(text_of(m.get("content")) for m in messages)
    entry = {
        "t": time.time(), "n_msgs": len(messages), "prompt_chars": prompt_chars, "stream": stream,
        "summarizer": system.startswith(SUMMARIZER_MARKER),
        "has_checkpoint": "[Context checkpoint" in joined,
        "first_words": user_text[:60],
        "contains": [tag for tag in ("TURN-0", "TURN-1", "TURN-5") if tag in joined],
    }
    with LOCK:
        LOG.append(entry)

    if entry["summarizer"]:
        summary = (
            "## Goal\nCHECKPOINT-SUMMARY of the earlier conversation\n"
            "## Accomplished\nEarlier questions were answered.\n## Remaining work\nContinue."
        )
        return JSONResponse(completion(cid, model, {"role": "assistant", "content": summary}, "stop", prompt_chars))

    d = parse_directives(user_text)
    label = d.get("label")
    steps = int(d.get("steps", 0))
    narrate = int(d.get("narrate", 0))
    overflow_at = int(d.get("overflow_at", -1))
    tool_names = {t.get("function", {}).get("name") for t in tools}
    sleep_tool = next((n for n in tool_names if n and n.endswith("sleep")), None)

    with LOCK:
        issued = ISSUED.get(label, 0) if label else 0
        overflow_now = label and overflow_at >= 0 and issued >= overflow_at and label not in OVERFLOWED
        if overflow_now:
            OVERFLOWED.add(label)
    if overflow_now:
        entry["overflow"] = True
        return JSONResponse(status_code=400, content={"error": {
            "message": "This model's maximum context length is 6000 tokens. However, your messages resulted in 9000 tokens.",
            "type": "invalid_request_error", "code": "context_length_exceeded", "param": "messages"}})

    if label and sleep_tool and issued < steps:
        with LOCK:
            ISSUED[label] = issued + 1
        narration = f"Step {issued + 1} for {label}: " + " ".join(f"note{issued}x{i}" for i in range(narrate))
        call = {"id": "call_" + uuid.uuid4().hex[:8], "type": "function", "function": {
            "name": sleep_tool, "arguments": json.dumps({"seconds": 0.1, "reason": f"{label} step {issued + 1}"})}}
        if stream:
            return stream_response(cid, model, narration, call, prompt_chars)
        return JSONResponse(completion(cid, model, {"role": "assistant", "content": narration, "tool_calls": [call]}, "tool_calls", prompt_chars))

    text = f"FINAL[{label}] done after {issued} steps" if label else "ECHO: " + " ".join(user_text.split()[:8])
    if stream:
        return stream_response(cid, model, text, None, prompt_chars)
    return JSONResponse(completion(cid, model, {"role": "assistant", "content": text}, "stop", prompt_chars))


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "compaction-mock", "object": "model"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_LLM_PORT", "8043")), log_level="warning")
