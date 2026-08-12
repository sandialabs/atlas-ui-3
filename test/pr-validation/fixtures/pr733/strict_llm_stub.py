#!/usr/bin/env python3
"""OpenAI-compatible stub that validates tool schemas the way providers do.

Providers require a function's `parameters` to be a JSON Schema of type
"object" and reject the whole request otherwise. This stub applies that one
rule and returns the provider's error shape, so the validation script can
exercise the rejection path without provider credentials.

Port is taken from STRICT_LLM_PORT (default 8124).
"""

import json
import os
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


def validate_tools(tools):
    """Return a provider-shaped error body for the first invalid tool, else None."""
    for i, tool in enumerate(tools or []):
        function = tool.get("function") if isinstance(tool, dict) else None
        function = function if isinstance(function, dict) else {}
        parameters = function.get("parameters")
        if parameters is None:
            continue
        # A non-dict `parameters` is itself invalid; report it rather than
        # raising an AttributeError and answering the negative test with a 500.
        declared = parameters.get("type") if isinstance(parameters, dict) else parameters
        if declared != "object":
            return {
                "error": {
                    "message": (
                        f"Invalid schema for function '{function.get('name')}': schema must "
                        f"be a JSON Schema of 'type: \"object\"', got '{declared}'."
                    ),
                    "type": "invalid_request_error",
                    "param": f"tools[{i}].function.parameters",
                    "code": "invalid_function_parameters",
                }
            }
    return None


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    error = validate_tools(body.get("tools") or [])
    if error:
        return JSONResponse(status_code=400, content=error)

    text = "Stub reply: tool schemas accepted."
    created = int(time.time())
    completion_id = "chatcmpl-stub"
    model = body.get("model", "stub")

    if body.get("stream"):
        def generate():
            first = {
                "id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": text},
                             "finish_reason": None}],
            }
            yield f"data: {json.dumps(first)}\n\n"
            last = {
                "id": completion_id, "object": "chat.completion.chunk", "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(last)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return {
        "id": completion_id, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("STRICT_LLM_PORT", "8124")),
                log_level="warning")
