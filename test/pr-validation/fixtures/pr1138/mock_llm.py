"""Scripted OpenAI-compatible mock LLM for empty checkpoint recovery (#1138).

The mock separates two kinds of request by their system prompt:

* a **summarizer** request -- the compaction summarizer's prompt, always a plain
  completion -- is answered according to the current mode, so a turn can be made
  to fail the way the bug report describes: content `null` with `finish_reason`
  `stop` (a model that writes nothing), or a reasoning model that drained its
  budget (`finish_reason` `length`, reasoning tokens in the usage block);
* any other request is an ordinary chat turn, answered with a streamed echo.

Modes (`POST /test/mode` with `{"mode": ...}`, which also clears the log):
  `always_empty` every summarizer call returns no content, `finish_reason`
                 `stop`;
  `exhausted`    every summarizer call returns no content, `finish_reason`
                 `length`, reasoning tokens consuming the budget;
  `empty_once`   the first summarizer call returns no content, later ones
                 return a checkpoint (the transient case).

`GET /test/log` returns one entry per request: whether it was a summarizer
call, its attempt index, whether the mock's answer was blank, the finish reason
it answered with, the model and output budget the backend asked for, and the
first words of the newest user message.
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
MODE = {"value": os.environ.get("MOCK_MODE", "always_empty")}
SUMMARIZER_CALLS = {"n": 0}
LOCK = threading.Lock()
SUMMARIZER_MARKER = "You compact a long conversation"
CHECKPOINT = (
    "## Goal\nCHECKPOINT-SUMMARY of the earlier conversation\n"
    "## Accomplished\nEarlier questions were answered.\n## Remaining work\nContinue."
)


def text_of(content):
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def last_user(messages):
    for message in reversed(messages):
        if message.get("role") == "user":
            return text_of(message.get("content"))
    return ""


def _sse(payload):
    return f"data: {json.dumps(payload)}\n\n"


def _completion(cid, model, message, finish_reason, usage):
    return {
        "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _stream(cid, model, content):
    def generate():
        base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                "model": model}
        yield _sse({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                        "finish_reason": None}]})
        for word in content.split(" "):
            yield _sse({**base, "choices": [{"index": 0, "delta": {"content": word + " "},
                                            "finish_reason": None}]})
        yield _sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield _sse({**base, "choices": [], "usage": {
            "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


def summarizer_reply(mode, attempt, budget):
    """The (message, finish_reason, usage) the mock answers a summarizer with."""
    plain_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    if mode == "exhausted":
        # All of the budget went to reasoning, so there is no final text.
        return ({"role": "assistant", "content": None}, "length",
                {"prompt_tokens": 100, "completion_tokens": budget,
                 "completion_tokens_details": {"reasoning_tokens": max(0, budget - 50)},
                 "total_tokens": 100 + budget})
    if mode == "empty_once" and attempt > 0:
        return {"role": "assistant", "content": CHECKPOINT}, "stop", plain_usage
    return {"role": "assistant", "content": None}, "stop", plain_usage


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test/log")
async def get_log():
    return LOG


@app.post("/test/mode")
async def set_mode(request: Request):
    """Switch scripted behaviour and clear the request log."""
    body = await request.json()
    with LOCK:
        MODE["value"] = body.get("mode", "always_empty")
        SUMMARIZER_CALLS["n"] = 0
        LOG.clear()
    return {"mode": MODE["value"]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "empty-checkpoint-mock")
    budget = body.get("max_tokens")
    stream = bool(body.get("stream"))
    prompt_chars = sum(len(text_of(message.get("content"))) for message in messages)
    system = text_of(messages[0].get("content")) if messages and messages[0].get("role") == "system" else ""
    is_summarizer = system.startswith(SUMMARIZER_MARKER)
    joined = "\n".join(text_of(message.get("content")) for message in messages)
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]

    with LOCK:
        if is_summarizer:
            attempt = SUMMARIZER_CALLS["n"]
            SUMMARIZER_CALLS["n"] += 1
        else:
            attempt = None
        if is_summarizer:
            message, finish_reason, usage = summarizer_reply(MODE["value"], attempt, budget or 0)
            content = message.get("content") or ""
        else:
            message = {"role": "assistant", "content": "ECHO: " + " ".join(last_user(messages).split()[:8])}
            content = message["content"]
            finish_reason, usage = "stop", {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        LOG.append({
            "summarizer": is_summarizer,
            "attempt": attempt,
            "empty": is_summarizer and not content,
            "finish_reason": finish_reason,
            "max_tokens": budget,
            "model": model,
            "prompt_chars": prompt_chars,
            "first_words": last_user(messages)[:60],
            "has_checkpoint": "[Context checkpoint" in joined,
        })

    if stream:
        return _stream(cid, model, content)
    return JSONResponse(_completion(cid, model, message, finish_reason, usage))


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "empty-checkpoint-mock", "object": "model"},
                                       {"id": "reasoning-mock", "object": "model"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("MOCK_LLM_PORT", "8044")),
            log_level="warning")
