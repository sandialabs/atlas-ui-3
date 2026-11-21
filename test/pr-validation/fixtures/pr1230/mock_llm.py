"""OpenAI-compatible mock of a reasoning model that enforces the reasoning
round-trip (issue #1230).

* A request whose last message is the user turn gets a tool call to the first
  offered tool, together with ``reasoning_content``.
* A follow-up request (after tool results) is rejected with HTTP 400 -- the
  provider error quoted in the issue -- unless every assistant tool-call
  message carries back exactly the ``reasoning_content`` it was produced with.
  Otherwise it answers ``FINAL-ANSWER-OK``.

Both streaming (SSE) and non-streaming responses are served. ``/test/log``
reports what each request carried, for the validation script to assert on.
"""
import json
import os
import time
import uuid

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

REASONING = "PR1230-HIDDEN-REASONING: call the tool first."
app = FastAPI()
LOG = []


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


def chunk(cid, model, delta, finish_reason=None):
    return sse({"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]})


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
    stream = bool(body.get("stream"))
    model = body.get("model", "strict-reasoner")
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]

    entry = {"stream": stream, "n_msgs": len(messages), "assistant_tool_calls": []}
    LOG.append(entry)
    for i, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ok = m.get("reasoning_content") == REASONING
            entry["assistant_tool_calls"].append({"index": i, "reasoning_ok": ok})
            if not ok:
                entry["rejected"] = True
                return JSONResponse(status_code=400, content={"error": {
                    "message": (f"The assistant turn at messages[{i}] is missing its reasoning. "
                                "Send it back in 'reasoning_content', or set "
                                "allow_missing_thinking=true to skip this check."),
                    "type": "invalid_request_error", "code": 400}})

    if messages and messages[-1].get("role") == "user" and tools:
        call = {"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                "function": {"name": tools[0], "arguments": json.dumps({"content": "# Notes\nhello"})}}
        if stream:
            def gen():
                half = len(REASONING) // 2
                yield chunk(cid, model, {"role": "assistant", "reasoning_content": REASONING[:half]})
                yield chunk(cid, model, {"reasoning_content": REASONING[half:]})
                yield chunk(cid, model, {"tool_calls": [{"index": 0, **call}]})
                yield chunk(cid, model, {}, "tool_calls")
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        return JSONResponse({"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                             "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                                 "role": "assistant", "content": None, "reasoning_content": REASONING,
                                 "tool_calls": [call]}}],
                             "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})

    text = "FINAL-ANSWER-OK"
    if stream:
        def gen2():
            yield chunk(cid, model, {"role": "assistant", "reasoning_content": "wrap up"})
            yield chunk(cid, model, {"content": text})
            yield chunk(cid, model, {}, "stop")
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen2(), media_type="text/event-stream")
    return JSONResponse({"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
                         "choices": [{"index": 0, "finish_reason": "stop", "message": {
                             "role": "assistant", "content": text, "reasoning_content": "wrap up"}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MOCK_LLM_PORT"]), log_level="warning")
