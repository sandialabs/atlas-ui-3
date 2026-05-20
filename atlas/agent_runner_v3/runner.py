"""Container-side agent loop for Atlas Agent Portal V3.

Reads the run configuration from environment variables (set by the K8s
Job spec built in atlas/modules/agent_portal_v3/job_template.py), runs a
ReAct-style loop against the chosen LLM, executes MCP tool calls over
streamable HTTP, and prints structured progress to stdout so the orches-
trator can show the user what's happening.

Env contract (set by atlas backend):
    ATLAS_RUN_ID         -- uuid, echoed in every line for log correlation
    ATLAS_USER_EMAIL     -- run owner (used for audit lines)
    ATLAS_PROMPT         -- the user task
    ATLAS_MCP_CONFIG     -- JSON: {server_name: {transport, url}, ...}
    ATLAS_LLM_PROVIDER   -- anthropic|openai|google|openrouter|groq|mistral
    ATLAS_LLM_MODEL      -- model id (provider/model or bare)
    {PROVIDER}_API_KEY   -- provider-specific key var
    ATLAS_MAX_ITERATIONS -- optional cap, default 10
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from typing import Any, Dict, List, Optional

import httpx


def log(kind: str, msg: str, **extra: Any) -> None:
    payload: Dict[str, Any] = {
        "ts": time.time(),
        "run_id": os.environ.get("ATLAS_RUN_ID", ""),
        "kind": kind,
        "msg": msg,
    }
    if extra:
        payload["extra"] = extra
    print(json.dumps(payload), flush=True)


# ---------------- MCP Streamable HTTP client (minimal) ----------------

class MCPHttpClient:
    """A tiny client for MCP servers reachable over Streamable HTTP /sse.

    We do just enough for: initialize, tools/list, tools/call. JSON-RPC 2.0
    over POST. Each response is parsed from the SSE-style event stream or a
    plain JSON body, whichever the server returns.
    """

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url.rstrip("/")
        self._id = 0
        self._http = httpx.AsyncClient(timeout=60.0)
        self._session_id: Optional[str] = None
        self.tools: List[Dict[str, Any]] = []

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            body["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = await self._http.post(self.url, json=body, headers=headers)
        new_session = resp.headers.get("Mcp-Session-Id")
        if new_session:
            self._session_id = new_session

        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            # parse the first JSON-RPC response we see
            text = resp.text
            for chunk in text.split("\n\n"):
                for line in chunk.splitlines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload:
                        continue
                    try:
                        msg = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("id") == body["id"]:
                        if "error" in msg:
                            raise RuntimeError(msg["error"])
                        return msg.get("result")
            raise RuntimeError("MCP server returned event-stream with no matching id")
        # Plain JSON
        msg = resp.json()
        if isinstance(msg, dict) and "error" in msg:
            raise RuntimeError(msg["error"])
        return msg.get("result") if isinstance(msg, dict) else msg

    async def initialize(self) -> None:
        await self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "atlas-agent-runner-v3", "version": "1"},
            },
        )
        # Servers expect an initialized notification before regular use.
        try:
            await self._http.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    **(
                        {"Mcp-Session-Id": self._session_id}
                        if self._session_id
                        else {}
                    ),
                },
            )
        except Exception as e:  # noqa: BLE001
            log("warn", f"MCP {self.name}: notifications/initialized failed: {e}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._rpc("tools/list", {})
        tools = (result or {}).get("tools", []) if isinstance(result, dict) else []
        self.tools = tools
        return tools

    async def call_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._rpc("tools/call", {"name": name, "arguments": args})
        return result or {}

    async def close(self) -> None:
        await self._http.aclose()


# ---------------- LLM client ----------------

class LLM:
    def __init__(self, provider: str, model: str):
        self.provider = (provider or "anthropic").lower()
        self.model = model
        if self.provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY missing")
            self.base = "https://api.anthropic.com/v1/messages"
            self.headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        elif self.provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY missing")
            self.base = "https://api.openai.com/v1/chat/completions"
            self.headers = {
                "Authorization": f"Bearer {key}",
                "content-type": "application/json",
            }
        else:
            raise RuntimeError(f"Unsupported provider in runner: {self.provider}")
        self._http = httpx.AsyncClient(timeout=120.0)

    async def call(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: str,
    ) -> Dict[str, Any]:
        if self.provider == "anthropic":
            body = {
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": messages,
            }
            if tools:
                body["tools"] = tools
            resp = await self._http.post(self.base, json=body, headers=self.headers)
            if resp.status_code >= 400:
                raise RuntimeError(f"anthropic error {resp.status_code}: {resp.text}")
            return resp.json()
        # OpenAI
        body = {"model": self.model, "messages": [{"role": "system", "content": system}] + messages}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        resp = await self._http.post(self.base, json=body, headers=self.headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"openai error {resp.status_code}: {resp.text}")
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()


# ---------------- Tool registry ----------------

def _flatten_tools(clients: List[MCPHttpClient]) -> tuple[List[Dict[str, Any]], Dict[str, MCPHttpClient]]:
    flat: List[Dict[str, Any]] = []
    routing: Dict[str, MCPHttpClient] = {}
    for c in clients:
        for t in c.tools:
            qualified = f"{c.name}__{t['name']}"
            routing[qualified] = c
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            flat.append(
                {
                    "name": qualified,
                    "description": t.get("description", "")[:512],
                    "input_schema": schema,
                }
            )
    return flat, routing


def _to_openai_function_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


# ---------------- Agent loop ----------------

async def run_agent() -> int:
    prompt = os.environ.get("ATLAS_PROMPT", "").strip()
    if not prompt:
        log("error", "ATLAS_PROMPT is empty; nothing to do")
        return 2

    provider = os.environ.get("ATLAS_LLM_PROVIDER", "anthropic")
    model = os.environ.get("ATLAS_LLM_MODEL") or (
        "claude-haiku-4-5" if provider == "anthropic" else "gpt-4o-mini"
    )
    max_iter = int(os.environ.get("ATLAS_MAX_ITERATIONS", "10"))

    log("status", "starting", provider=provider, model=model, max_iter=max_iter)

    mcp_config_raw = os.environ.get("ATLAS_MCP_CONFIG", "{}")
    try:
        mcp_config = json.loads(mcp_config_raw)
    except json.JSONDecodeError as e:
        log("error", f"ATLAS_MCP_CONFIG is not valid JSON: {e}")
        return 2

    # Spin up MCP clients
    clients: List[MCPHttpClient] = []
    for name, cfg in mcp_config.items():
        url = cfg.get("url")
        if not url:
            log("warn", f"skipping MCP server {name}: no url")
            continue
        c = MCPHttpClient(name, url)
        try:
            await c.initialize()
            tools = await c.list_tools()
            log("mcp", f"connected to {name} ({len(tools)} tools)")
            clients.append(c)
        except Exception as e:  # noqa: BLE001
            log("warn", f"MCP {name} init failed: {e}")
            await c.close()

    tools, routing = _flatten_tools(clients)
    log("tools", f"{len(tools)} tools available", names=[t["name"] for t in tools])

    llm = LLM(provider, model)
    system = (
        "You are an automated Atlas agent running in a Kubernetes Job. "
        "Use the provided tools to complete the user's task. "
        "Stop and produce a final answer when done. Avoid asking the user "
        "questions -- there is no interactive user."
    )

    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]

    final_text: str = ""
    try:
        for step in range(1, max_iter + 1):
            log("turn", f"LLM turn {step}/{max_iter}")
            if provider == "anthropic":
                resp = await llm.call(messages, tools, system)
                content = resp.get("content", [])
                tool_uses = []
                texts = []
                for block in content:
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_uses.append(block)
                if texts:
                    final_text = "\n".join(texts)
                    log("assistant", final_text[:2000])
                if not tool_uses:
                    log("status", "done -- model produced final text")
                    break
                # Execute tools, append results
                messages.append({"role": "assistant", "content": content})
                tool_results = []
                for tu in tool_uses:
                    tname = tu["name"]
                    targs = tu.get("input", {}) or {}
                    client = routing.get(tname)
                    log("tool_call", tname, args=targs)
                    if not client:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu["id"],
                                "content": f"Tool {tname} not found",
                                "is_error": True,
                            }
                        )
                        continue
                    try:
                        bare = tname.split("__", 1)[1]
                        result = await client.call_tool(bare, targs)
                        as_text = json.dumps(result)[:8000]
                        log("tool_result", tname, preview=as_text[:200])
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu["id"],
                                "content": as_text,
                            }
                        )
                    except Exception as e:  # noqa: BLE001
                        log("tool_error", tname, error=str(e))
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu["id"],
                                "content": f"error: {e}",
                                "is_error": True,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})
            else:
                # OpenAI flavor
                resp = await llm.call(messages, _to_openai_function_tools(tools), system)
                choice = resp["choices"][0]["message"]
                if choice.get("content"):
                    final_text = choice["content"]
                    log("assistant", final_text[:2000])
                tool_calls = choice.get("tool_calls") or []
                if not tool_calls:
                    break
                messages.append(choice)
                for tc in tool_calls:
                    tname = tc["function"]["name"]
                    try:
                        targs = json.loads(tc["function"].get("arguments", "{}"))
                    except json.JSONDecodeError:
                        targs = {}
                    client = routing.get(tname)
                    log("tool_call", tname, args=targs)
                    if not client:
                        result_str = f"Tool {tname} not found"
                    else:
                        try:
                            bare = tname.split("__", 1)[1]
                            r = await client.call_tool(bare, targs)
                            result_str = json.dumps(r)[:8000]
                            log("tool_result", tname, preview=result_str[:200])
                        except Exception as e:  # noqa: BLE001
                            log("tool_error", tname, error=str(e))
                            result_str = f"error: {e}"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result_str,
                        }
                    )
        else:
            log("status", "iteration cap reached")
    finally:
        for c in clients:
            await c.close()
        await llm.close()

    log("final", final_text or "(no final text produced)")
    return 0


def main() -> int:
    try:
        return asyncio.run(run_agent())
    except Exception as e:  # noqa: BLE001
        log("error", f"fatal: {e}", trace=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
