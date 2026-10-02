#!/usr/bin/env python3
"""Mock enterprise LiteLLM proxy with team-scoped models.

Implements the three LiteLLM proxy calls Atlas's team-then-model selection
uses, with the same request and response shapes as a real LiteLLM proxy:

    GET  /team/list?user_id=<id>     list of team objects
    GET  /models?team_id=<team_id>   {"object": "list", "data": [{"id": ...}]}
    POST /chat/completions           requires the x-litellm-team-id header

``/v1/models`` and ``/v1/chat/completions`` are accepted as aliases, as on a
real proxy.

Two kinds of bearer credential are accepted:

- the master key (``MOCK_LITELLM_MASTER_KEY``, default
  ``sk-mock-litellm-master``): a service key that may act for any user, the
  shape used by Atlas gateways with ``auth_type: "system"``;
- a JWT whose ``oid`` claim names a known user, the shape of the Entra
  On-Behalf-Of token used by ``auth_type: "delegated"``. The mock does not
  verify signatures -- it stands in for LiteLLM's JWT auth, not the IdP.

Chat completions are refused unless the team header names a team the caller
belongs to (for the master key: any team) and the model is one that team may
call. Every chat request is recorded; ``GET /mock/requests`` returns them so a
test can confirm exactly which team header reached the proxy.
"""

import base64
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

MASTER_KEY = os.environ.get("MOCK_LITELLM_MASTER_KEY", "sk-mock-litellm-master")
PORT = int(os.environ.get("MOCK_LITELLM_PORT", "4010"))
TEAM_HEADER = "x-litellm-team-id"

# user_id -> Entra-style object id, so delegated (JWT) callers resolve too.
USERS: Dict[str, str] = {
    "test@test.com": "0b0c6f2e-0000-4000-8000-000000000001",
    "alice@example.com": "0b0c6f2e-0000-4000-8000-000000000002",
    "bob@example.com": "0b0c6f2e-0000-4000-8000-000000000003",
}

TEAMS: List[Dict[str, Any]] = [
    {
        "team_id": "team-alpha-7f3a",
        "team_alias": "Project Alpha",
        "models": ["gpt-4o-mini", "claude-sonnet"],
        "members": ["test@test.com", "alice@example.com"],
    },
    {
        "team_id": "team-beta-19c2",
        "team_alias": "Project Beta",
        "models": ["llama-3.3-70b"],
        "members": ["test@test.com", "bob@example.com"],
    },
    {
        "team_id": "team-gamma-5d81",
        "team_alias": "Project Gamma",
        "models": ["gpt-4o-mini"],
        "members": ["bob@example.com"],
    },
]

app = FastAPI(title="Mock LiteLLM Proxy", description="Team-scoped LiteLLM proxy for testing")

_request_log: List[Dict[str, Any]] = []


def _error(status: int, message: str, error_type: str = "auth_error") -> JSONResponse:
    """LiteLLM's error envelope."""
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type, "param": None, "code": str(status)}},
    )


def _jwt_claims(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def _caller(request: Request) -> Optional[Dict[str, Any]]:
    """Who is calling: {"admin": True} for the master key, or {"user_id": ...}."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if token == MASTER_KEY:
        return {"admin": True, "user_id": None}
    oid = _jwt_claims(token).get("oid")
    for user_id, user_oid in USERS.items():
        if oid and oid == user_oid:
            return {"admin": False, "user_id": user_id, "oid": oid}
    return None


def _resolve_user(user_id: str) -> Optional[str]:
    """Accept a user id as either the email or the object id."""
    if user_id in USERS:
        return user_id
    for email, oid in USERS.items():
        if oid == user_id:
            return email
    return None


def _team(team_id_or_alias: str) -> Optional[Dict[str, Any]]:
    # LiteLLM accepts the team id or its alias in x-litellm-team-id.
    for team in TEAMS:
        if team_id_or_alias in (team["team_id"], team["team_alias"]):
            return team
    return None


def _may_use_team(caller: Dict[str, Any], team: Dict[str, Any]) -> bool:
    return caller["admin"] or caller["user_id"] in team["members"]


def _team_payload(team: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "team_id": team["team_id"],
        "team_alias": team["team_alias"],
        "models": list(team["models"]),
        "members_with_roles": [{"user_id": m, "role": "user"} for m in team["members"]],
        "blocked": False,
        "spend": 0.0,
        "max_budget": None,
    }


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "litellm-mock"}


@app.get("/team/list")
async def team_list(request: Request, user_id: Optional[str] = None):
    caller = _caller(request)
    if caller is None:
        return _error(401, "Authentication Error, invalid or missing bearer token")
    if user_id is None:
        if not caller["admin"]:
            return _error(401, "Only proxy admins may list all teams; pass user_id")
        return [_team_payload(team) for team in TEAMS]
    target = _resolve_user(user_id)
    if not caller["admin"] and target != caller["user_id"]:
        return _error(401, "A user may only list their own teams")
    if target is None:
        return []
    return [_team_payload(team) for team in TEAMS if target in team["members"]]


@app.get("/models")
@app.get("/v1/models")
async def list_models(request: Request, team_id: Optional[str] = None):
    caller = _caller(request)
    if caller is None:
        return _error(401, "Authentication Error, invalid or missing bearer token")
    if team_id:
        team = _team(team_id)
        if team is None:
            return _error(404, f"Team not found: {team_id}", "not_found_error")
        if not _may_use_team(caller, team):
            return _error(401, f"User is not a member of team {team_id}")
        model_ids = team["models"]
    elif caller["admin"]:
        model_ids = sorted({m for team in TEAMS for m in team["models"]})
    else:
        model_ids = sorted({m for team in TEAMS if caller["user_id"] in team["members"] for m in team["models"]})
    created = int(time.time())
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": created, "owned_by": "openai"} for m in model_ids],
    }


def _reply_text(team: Dict[str, Any], model: str, messages: List[Dict[str, Any]]) -> str:
    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            last_user = str(content or "")
            break
    return (
        f"[{team['team_alias']} / {model}] Mock LiteLLM reply to: {last_user[:200]}"
    )


def _stream(completion_id: str, model: str, text: str):
    created = int(time.time())
    words = text.split(" ")
    for index, word in enumerate(words):
        piece = word if index == len(words) - 1 else word + " "
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk)}\n\n"
    final = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/chat/completions")
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    caller = _caller(request)
    team_header = request.headers.get(TEAM_HEADER)
    model = body.get("model", "")
    record = {
        "timestamp": time.time(),
        "path": request.url.path,
        "model": model,
        "team_header": team_header,
        "caller": "master_key" if caller and caller["admin"] else (caller or {}).get("user_id"),
        "customer_id": request.headers.get("x-litellm-customer-id"),
        "stream": bool(body.get("stream")),
        "message_count": len(body.get("messages") or []),
    }
    _request_log.append(record)
    del _request_log[:-200]

    if caller is None:
        record["outcome"] = "unauthenticated"
        return _error(401, "Authentication Error, invalid or missing bearer token")
    if not team_header:
        record["outcome"] = "missing_team"
        return _error(400, f"Missing {TEAM_HEADER} header: select the team to charge", "bad_request_error")
    team = _team(team_header)
    if team is None or not _may_use_team(caller, team):
        record["outcome"] = "team_denied"
        return _error(401, f"Caller may not use team {team_header}")
    if model not in team["models"]:
        record["outcome"] = "model_denied"
        return _error(401, f"team not allowed to access model. Team={team['team_id']}, Model={model}")
    record["outcome"] = "ok"
    record["team_id"] = team["team_id"]

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    text = _reply_text(team, model, body.get("messages") or [])
    if body.get("stream"):
        return StreamingResponse(_stream(completion_id, model, text), media_type="text/event-stream")
    prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in body.get("messages") or [])
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(text.split()),
            "total_tokens": prompt_tokens + len(text.split()),
        },
    }


@app.get("/mock/requests")
async def mock_requests():
    """Chat requests received, oldest first (test support, not a LiteLLM API)."""
    return {"requests": list(_request_log)}


@app.delete("/mock/requests")
async def clear_mock_requests():
    _request_log.clear()
    return {"cleared": True}


if __name__ == "__main__":
    print(f"Mock LiteLLM proxy on http://127.0.0.1:{PORT} (master key: {MASTER_KEY})")
    uvicorn.run(app, host="127.0.0.1", port=PORT)
