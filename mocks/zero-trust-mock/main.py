#!/usr/bin/env python3
"""Zero-trust mock policy server.

A deliberately tiny HTTP service that decides, at runtime, what an Atlas hook
should do with an operation:

  POST /v1/authorize   <- the hook envelope (JSON), -> a hook decision (JSON)
  GET  /health         -> {"status": "ok"}
  GET  /decisions      -> the recent decision log (for demos and assertions)
  POST /decisions/reset

The point of the demo is the *split*: policy lives in a service that an
operator can change and audit centrally, while the hook on the Atlas side stays
a five-line forwarder (``hook_client.py``). Because every event's envelope has
the same shape, the same endpoint can back PreToolUse, PermissionRequest,
UserPromptSubmit, RagCall, and the rest.

Run:  python mocks/zero-trust-mock/main.py     # listens on :8099
"""

from __future__ import annotations

import os
from collections import deque
from typing import Any, Deque, Dict

from fastapi import FastAPI, Request
from policy import evaluate

MAX_LOG = 100
_decisions: Deque[Dict[str, Any]] = deque(maxlen=MAX_LOG)


def get_app() -> FastAPI:
    app = FastAPI(title="Zero-Trust Mock Policy Server", version="1.0.0")

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/authorize")
    async def authorize(request: Request) -> Dict[str, Any]:
        envelope = await request.json()
        decision = evaluate(envelope)
        payload = envelope.get("payload") or {}
        # Log a projection, not the envelope: payloads carry prompt text,
        # retrieved documents, and tokenized URLs.
        _decisions.append({
            "event": envelope.get("hook_event_name"),
            "user_email": envelope.get("user_email"),
            "tool_name": payload.get("tool_name"),
            "decision": decision["decision"],
            "reason": decision["reason"],
        })
        return decision

    @app.get("/decisions")
    async def decisions() -> Dict[str, Any]:
        return {"count": len(_decisions), "decisions": list(_decisions)}

    @app.post("/decisions/reset")
    async def reset() -> Dict[str, str]:
        _decisions.clear()
        return {"status": "cleared"}

    return app


app = get_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("ZERO_TRUST_PORT", "8099"))
    uvicorn.run(app, host="0.0.0.0", port=port)
