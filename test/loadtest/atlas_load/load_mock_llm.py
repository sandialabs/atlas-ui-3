"""The multi-replica harness's scripted mock LLM, plus request accounting and
optional verification of delegated bearer tokens.

The model behaviour (directives such as ``tool=sleep sleep=1 steps=3``) is the
harness's own ``test/multireplica/harness/mock_llm.py``, imported unchanged.
This wrapper adds:

- ``GET /stats``: completions served, and how many carried a valid or an
  invalid delegated token;
- when ``MOCK_LLM_JWKS_URL`` is set, every ``/v1/chat/completions`` request
  must carry ``Authorization: Bearer <jwt>`` signed by that JWKS, with
  ``MOCK_LLM_AUDIENCE`` as its audience (the token Atlas minted by RFC 8693
  exchange for a model with ``api_key_source: "delegated"``); anything else
  gets a 401, the way a real LiteLLM gateway would answer.

Run: ``MOCK_LLM_PORT=... [MOCK_LLM_JWKS_URL=...] python load_mock_llm.py``.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "multireplica" / "harness"))
import mock_llm  # noqa: E402  (the harness's scripted model, imported by path)

app = mock_llm.app
STATS: Counter = Counter()
JWKS_URL = os.environ.get("MOCK_LLM_JWKS_URL", "")
AUDIENCE = os.environ.get("MOCK_LLM_AUDIENCE", "mock-litellm")
_jwks_client = None


def _verify(header: str) -> bool:
    global _jwks_client
    import jwt

    if not header.startswith("Bearer "):
        return False
    token = header[7:]
    try:
        if _jwks_client is None:
            _jwks_client = jwt.PyJWKClient(JWKS_URL, cache_jwk_set=True, lifespan=300)
        key = _jwks_client.get_signing_key_from_jwt(token).key
        jwt.decode(token, key, algorithms=["RS256"], audience=AUDIENCE, leeway=5)
        return True
    except Exception:
        return False


@app.middleware("http")
async def account(request: Request, call_next):
    if request.url.path != "/v1/chat/completions":
        return await call_next(request)
    STATS["completions"] += 1
    if JWKS_URL:
        if not _verify(request.headers.get("authorization", "")):
            STATS["auth_rejected"] += 1
            return JSONResponse(status_code=401, content={"error": {
                "message": "invalid delegated token", "type": "authentication_error", "code": "invalid_token"}})
        STATS["auth_verified"] += 1
    return await call_next(request)


@app.get("/stats")
async def stats():
    return dict(STATS)


@app.on_event("startup")
async def _widen_thread_pool() -> None:
    # The harness mock streams from a synchronous generator (time.sleep per
    # token), which Starlette runs on anyio's thread pool: 40 threads by
    # default, which would cap the whole load test at 40 concurrent streams
    # and measure the mock instead of Atlas.
    import anyio.to_thread

    anyio.to_thread.current_default_thread_limiter().total_tokens = int(
        os.environ.get("MOCK_LLM_THREADS", "1000"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MOCK_LLM_PORT"]), log_level="warning")
