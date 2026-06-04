"""REST routes for Agent Portal V3 -- K8s-Job-backed agent runs.

Endpoints (all under /api/agent-portal-v3):
- GET  /capabilities              -> cluster reachable? image? namespace?
- GET  /mcp-servers               -> selectable MCP servers (subset of mcp.json)
- GET  /models                    -> selectable LLM models
- GET  /runs                      -> list current user's runs
- POST /runs                      -> launch a run
- GET  /runs/{id}                 -> single run
- POST /runs/{id}/cancel          -> cancel running job
- DELETE /runs/{id}               -> cancel + remove record
- GET  /runs/{id}/logs            -> tail of pod logs (text)
- GET  /runs/{id}/events          -> append-only event log
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from atlas.core.log_sanitizer import get_current_user
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.agent_portal_v3 import RunNotFoundError
from atlas.modules.agent_portal_v3.runner import get_agent_runner, serialize_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-portal-v3", tags=["agent-portal-v3"])


def _require_enabled():
    app_settings = app_factory.get_config_manager().app_settings
    if not getattr(app_settings, "feature_agent_portal_v3_enabled", False):
        raise HTTPException(status_code=404, detail="Agent portal v3 is disabled")


def _llm_keys_from_settings() -> Dict[str, str]:
    """Read provider keys from env (AppSettings doesn't model them)."""
    import os
    keys: Dict[str, str] = {}
    pairs = [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("gemini", "GOOGLE_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("cerebras", "CEREBRAS_API_KEY"),
    ]
    placeholders = {
        "your_google_api_key_here",
        "your_cerebras_api_key_here",
        "your_openai_api_key_here",
        "your_anthropic_api_key_here",
        "",
    }
    for provider, env_name in pairs:
        val = os.environ.get(env_name) or ""
        if val and val.strip() not in placeholders:
            keys[provider] = val
    return keys


def _runner():
    r = get_agent_runner()
    r.set_llm_keys(_llm_keys_from_settings())
    return r


# ---- request models ----

class LaunchRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Agent task / system instruction.")
    mcp_servers: List[str] = Field(
        default_factory=list,
        description="Server names from mcp.json the agent is allowed to use.",
    )
    llm_model: str = Field(..., description="Model id (e.g. claude-haiku-4-5).")
    llm_provider: str = Field(
        default="anthropic",
        description="Provider key (anthropic, openai, google, ...).",
    )
    display_name: str = Field(default="", max_length=200)
    egress_check: bool = Field(
        default=False,
        description=(
            "Run a network egress self-check at startup that probes allowed "
            "(public) vs blocked (private/link-local) destinations and logs "
            "the result -- demonstrates the per-run NetworkPolicy."
        ),
    )


# ---- read endpoints ----

@router.get("/capabilities")
async def capabilities(current_user: str = Depends(get_current_user)):
    _require_enabled()
    from atlas.modules.agent_portal_v3 import k8s_client
    reachable = await k8s_client.cluster_reachable()
    runner = _runner()
    return {
        "cluster_reachable": reachable,
        "namespace": runner.namespace,
        "image": runner.image,
        "providers_configured": sorted(_llm_keys_from_settings().keys()),
    }


@router.get("/mcp-servers")
async def list_mcp_servers(current_user: str = Depends(get_current_user)):
    _require_enabled()
    cm = app_factory.get_config_manager()
    mcp_config = cm.mcp_config
    servers = []
    raw = getattr(mcp_config, "servers", {}) or {}
    for name, cfg in raw.items():
        # Only surface remote (http/sse) servers as launch-time options -- the
        # agent runs in a container without atlas's stdio binaries available.
        # We still allow stdio entries to render so users see they're filtered.
        transport = "stdio"
        url = None
        if isinstance(cfg, dict):
            url = cfg.get("url")
            transport = cfg.get("transport") or (
                "http" if url and url.startswith("http") else "stdio"
            )
            description = cfg.get("description", "") or cfg.get("short_description", "")
            groups = cfg.get("groups", [])
        else:
            description = getattr(cfg, "description", "") or ""
            url = getattr(cfg, "url", None)
            transport = getattr(cfg, "transport", None) or (
                "http" if url and url.startswith("http") else "stdio"
            )
            groups = getattr(cfg, "groups", []) or []
        servers.append(
            {
                "name": name,
                "transport": transport,
                "url": url,
                "description": description,
                "groups": groups,
                "selectable": transport in ("http", "sse"),
            }
        )
    servers.sort(key=lambda s: (not s["selectable"], s["name"]))
    return {"servers": servers}


@router.get("/models")
async def list_models(current_user: str = Depends(get_current_user)):
    _require_enabled()
    cm = app_factory.get_config_manager()
    llm_config = cm.llm_config
    models_meta = getattr(llm_config, "models", {}) or {}
    keys = set(_llm_keys_from_settings().keys())

    def _provider_from_model_id(model_id: str) -> str:
        m = (model_id or "").lower()
        if m.startswith("anthropic/") or "claude" in m:
            return "anthropic"
        if m.startswith("openai/") or m.startswith("gpt-") or "gpt-" in m:
            return "openai"
        if m.startswith("gemini/") or "gemini" in m:
            return "google"
        if m.startswith("openrouter/"):
            return "openrouter"
        if m.startswith("groq/"):
            return "groq"
        if m.startswith("mistral/"):
            return "mistral"
        return "anthropic"

    results = []
    for name, meta in models_meta.items():
        model_id = name
        if isinstance(meta, dict):
            model_id = meta.get("model_name") or name
            label = meta.get("description") or name
        else:
            model_id = getattr(meta, "model_name", None) or name
            label = getattr(meta, "description", None) or name
        provider = _provider_from_model_id(model_id)
        results.append(
            {
                "name": name,
                "model_id": model_id,
                "provider": provider,
                "label": label,
                "available": provider in keys,
            }
        )
    results.sort(key=lambda m: (not m["available"], m["name"]))
    return {"models": results}


@router.get("/runs")
async def list_runs(current_user: str = Depends(get_current_user)):
    _require_enabled()
    runner = _runner()
    records = runner.list_runs(current_user)
    return {"runs": [serialize_run(r, include_prompt=False) for r in records]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, current_user: str = Depends(get_current_user)):
    _require_enabled()
    runner = _runner()
    try:
        record = runner.get_run(run_id, current_user)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_run(record)


@router.get("/runs/{run_id}/logs")
async def run_logs(
    run_id: str,
    tail: int = 1000,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    runner = _runner()
    try:
        text = await runner.get_run_logs(run_id, current_user, tail_lines=tail)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run_id, "logs": text}


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    limit: int = 500,
    current_user: str = Depends(get_current_user),
):
    _require_enabled()
    runner = _runner()
    try:
        runner.get_run(run_id, current_user)  # auth check
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found")
    events = runner.list_events(run_id, limit=limit)
    return {
        "events": [
            {
                "id": e.id,
                "ts": e.ts.isoformat() if e.ts else None,
                "kind": e.kind,
                "message": e.message,
            }
            for e in events
        ]
    }


# ---- mutations ----

@router.post("/runs", status_code=201)
async def launch_run(
    req: LaunchRequest, current_user: str = Depends(get_current_user)
):
    _require_enabled()
    runner = _runner()

    cm = app_factory.get_config_manager()
    raw_mcp = getattr(cm.mcp_config, "servers", {}) or {}
    resolved: Dict[str, Any] = {}
    rejected: List[str] = []
    for name in req.mcp_servers:
        cfg = raw_mcp.get(name)
        if cfg is None:
            rejected.append(name)
            continue
        url = None
        transport = "stdio"
        if isinstance(cfg, dict):
            url = cfg.get("url")
            transport = cfg.get("transport") or (
                "http" if url and url.startswith("http") else "stdio"
            )
        else:
            url = getattr(cfg, "url", None)
            transport = getattr(cfg, "transport", None) or (
                "http" if url and url.startswith("http") else "stdio"
            )
        if transport not in ("http", "sse"):
            rejected.append(name)
            continue
        resolved[name] = {
            "transport": transport,
            "url": url,
        }
    if rejected:
        logger.info("agent_portal_v3 launch: dropping non-remote MCPs: %s", rejected)

    extra_env = {"ATLAS_EGRESS_CHECK": "default"} if req.egress_check else None

    record = await runner.launch_run(
        user_email=current_user,
        prompt=req.prompt,
        mcp_servers=list(resolved.keys()),
        mcp_resolved=resolved,
        llm_provider=req.llm_provider,
        llm_model=req.llm_model,
        display_name=req.display_name,
        extra_env=extra_env,
    )
    payload = serialize_run(record)
    payload["dropped_mcp_servers"] = rejected
    return payload


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, current_user: str = Depends(get_current_user)):
    _require_enabled()
    runner = _runner()
    try:
        record = await runner.cancel_run(run_id, current_user)
    except RunNotFoundError:
        raise HTTPException(status_code=404, detail="Run not found")
    return serialize_run(record)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str, current_user: str = Depends(get_current_user)):
    _require_enabled()
    runner = _runner()
    deleted = await runner.delete_run(run_id, current_user)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"ok": True}
