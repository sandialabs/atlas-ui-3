#!/usr/bin/env python3
"""OpenAI-compatible stub that enforces the tool-message ordering contract.

Azure/OpenAI require every assistant message carrying `tool_calls` to be
followed *immediately* by one `tool` message per `tool_call_id`. Anything
injected in between orphans the remaining ids and the whole request is
rejected. This stub applies exactly that rule and returns the provider's error
shape, so the validation script can exercise the real failure without provider
credentials.

It also scripts a two-round tool conversation: the first call asks for a tool,
the second (the continuation round, the one that carries the assistant
tool_calls block plus its replies) answers in plain text.

Port is taken from STRICT_ORDER_LLM_PORT (default 8126). The request log is
exposed at GET /_requests for the validation script to inspect.
"""

import json
import os
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# One entry per completion request: the roles seen, in order, each tagged with a
# short content preview so the validation script can tell the retrieved-context
# system message apart from the system prompt.
REQUEST_LOG = []


RAG_CONTEXT_MARKERS = ("Retrieved context from", "Pre-synthesized answer from")


def summarize(messages):
    """Roles in order, with the RAG context message tagged `system:rag`."""
    out = []
    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system" and any(m in content for m in RAG_CONTEXT_MARKERS):
            role = "system:rag"
        out.append(role)
    return out


def find_ordering_violation(messages):
    """Return a provider-shaped error body for the first orphaned id, else None."""
    for i, msg in enumerate(messages or []):
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            continue
        expected = [tc.get("id") for tc in msg["tool_calls"]]
        answered = []
        for follow in messages[i + 1:]:
            if follow.get("role") != "tool":
                break
            answered.append(follow.get("tool_call_id"))
        missing = [tc_id for tc_id in expected if tc_id not in answered]
        if missing:
            return {
                "error": {
                    "message": (
                        "An assistant message with 'tool_calls' must be followed by "
                        "tool messages responding to each 'tool_call_id'. The "
                        f"following tool_call_ids did not have response messages: "
                        f"{', '.join(missing)}"
                    ),
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": None,
                }
            }
    return None


def _tool_call_chunk(created, completion_id, model):
    return {
        "id": completion_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {
                "role": "assistant",
                "tool_calls": [{
                    "index": 0,
                    "id": "call_pr770StubToolCallId",
                    "type": "function",
                    "function": {"name": "orderdocs_lookup", "arguments": '{"title":"Lockout Tagout"}'},
                }],
            },
            "finish_reason": None,
        }],
    }


def _text_chunk(created, completion_id, model, text):
    return {
        "id": completion_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                     "finish_reason": None}],
    }


def _stop_chunk(created, completion_id, model, reason):
    return {
        "id": completion_id, "object": "chat.completion.chunk", "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
    }


@app.get("/_requests")
async def requests_log():
    return {"requests": REQUEST_LOG}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    REQUEST_LOG.append(summarize(messages))

    error = find_ordering_violation(messages)
    if error:
        return JSONResponse(status_code=400, content=error)

    # Round 1 asks for a tool; once a tool result is present, answer in text.
    wants_tool = (
        bool(body.get("tools"))
        and not any(m.get("role") == "tool" for m in messages)
    )
    text = "The Lockout Tagout document was retrieved and summarized."
    created = int(time.time())
    completion_id = "chatcmpl-pr770-stub"
    model = body.get("model", "stub")

    if body.get("stream"):
        def generate():
            if wants_tool:
                yield f"data: {json.dumps(_tool_call_chunk(created, completion_id, model))}\n\n"
                yield f"data: {json.dumps(_stop_chunk(created, completion_id, model, 'tool_calls'))}\n\n"
            else:
                yield f"data: {json.dumps(_text_chunk(created, completion_id, model, text))}\n\n"
                yield f"data: {json.dumps(_stop_chunk(created, completion_id, model, 'stop'))}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    if wants_tool:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_pr770StubToolCallId",
                "type": "function",
                "function": {"name": "orderdocs_lookup", "arguments": '{"title":"Lockout Tagout"}'},
            }],
        }
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": text}
        finish_reason = "stop"

    return {
        "id": completion_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1",
                port=int(os.environ.get("STRICT_ORDER_LLM_PORT", "8126")),
                log_level="warning")
