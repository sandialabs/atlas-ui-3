"""RAG MCP Aggregator Service (Phase 1: Discovery)

Aggregates discovery of RAG resources from authorized MCP servers that expose
the `rag_discover_resources` tool. Returns a flat list of data source IDs for
backward-compatible UI, with server-qualified IDs to avoid collisions.

Future phases will add search/synthesis and richer shapes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)
from core.prompt_risk import calculate_prompt_injection_risk, log_high_risk_event


class RAGMCPService:
    """Aggregator for RAG over MCP servers."""

    def __init__(self, mcp_manager, config_manager, auth_check_func) -> None:
        self.mcp_manager = mcp_manager
        self.config_manager = config_manager
        self.auth_check_func = auth_check_func

    async def discover_data_sources(self, username: str) -> List[str]:
        """Discover data sources across authorized MCP RAG servers.

        Phase 1 returns a flat list of strings for backward compatibility.
        Uses server-qualified IDs: "{server}:{resource_id}" to avoid collisions.
        """
        # Ensure RAG servers are initialized from rag_mcp_config, without polluting tool inventory
        try:
            rag_servers = self.config_manager.rag_mcp_config.servers
            # If these servers aren't in mcp_manager.clients, initialize just these
            missing = [name for name in rag_servers.keys() if name not in getattr(self.mcp_manager, "clients", {})]
            if missing:
                # Temporarily extend servers_config with rag servers and initialize them
                original = dict(getattr(self.mcp_manager, "servers_config", {}))
                try:
                    self.mcp_manager.servers_config.update({name: cfg.model_dump() for name, cfg in rag_servers.items()})
                    await self.mcp_manager.initialize_clients()
                    await self.mcp_manager.discover_tools()
                finally:
                    # Restore original list for general tools panel separation
                    self.mcp_manager.servers_config = original
        except Exception:
            # If anything goes wrong, fallback silently to existing clients
            pass
        try:
            # Determine servers current user can see
            authorized_servers: List[str] = self.mcp_manager.get_authorized_servers(
                username, self.auth_check_func
            )

            if not authorized_servers:
                logger.info("No authorized MCP servers for user %s", username)
                return []

            # Filter to servers that advertise the discovery tool
            servers_with_discovery: List[str] = []
            for server in authorized_servers:
                server_data = self.mcp_manager.available_tools.get(server)
                tool_list = (server_data or {}).get("tools", [])
                if any(getattr(t, "name", None) == "rag_discover_resources" for t in tool_list):
                    servers_with_discovery.append(server)

            if not servers_with_discovery:
                logger.info("No servers implement rag_discover_resources for user %s", username)
                return []

            # Fan out discovery calls
            sources: List[str] = []
            for server in servers_with_discovery:
                try:
                    raw = await self.mcp_manager.call_tool(
                        server_name=server,
                        tool_name="rag_discover_resources",
                        arguments={"username": username},
                    )

                    structured = self._extract_structured_result(raw)
                    resources = self._extract_resources(structured)
                    for r in resources:
                        rid = r.get("id") or r.get("name")
                        if not isinstance(rid, str):
                            continue
                        # Qualify with server to avoid collisions across providers
                        sources.append(f"{server}:{rid}")
                except Exception as e:
                    logger.warning(
                        "Discovery failed on server %s for user %s: %s",
                        server,
                        username,
                        e,
                    )

            # De-dupe while preserving order
            seen = set()
            deduped = []
            for s in sources:
                if s not in seen:
                    seen.add(s)
                    deduped.append(s)
            return deduped

        except Exception as e:
            logger.error("Error during RAG MCP discovery: %s", e, exc_info=True)
            return []

    async def discover_servers(self, username: str) -> List[Dict[str, Any]]:
        """Return richer per-server discovery structure for UI (rag_servers).

        Shape:
        [
          {
            "server": "docsRag",
            "displayName": "docsRag",
            "icon": <optional>,
            "sources": [
               {"id": "handbook", "name": "Employee Handbook", "authRequired": False, "selected": False}
            ]
          }
        ]
        """
        # Ensure RAG servers are initialized from rag_mcp_config, without polluting tool inventory
        try:
            rag_cfg_servers = self.config_manager.rag_mcp_config.servers
            missing = [name for name in rag_cfg_servers.keys() if name not in getattr(self.mcp_manager, "clients", {})]
            if missing:
                original = dict(getattr(self.mcp_manager, "servers_config", {}))
                try:
                    self.mcp_manager.servers_config.update({name: cfg.model_dump() for name, cfg in rag_cfg_servers.items()})
                    await self.mcp_manager.initialize_clients()
                    await self.mcp_manager.discover_tools()
                finally:
                    self.mcp_manager.servers_config = original
        except Exception:
            # Fallback silently if RAG config init fails; we'll just return empty set
            pass

        rag_servers: List[Dict[str, Any]] = []
        try:
            authorized_servers: List[str] = self.mcp_manager.get_authorized_servers(
                username, self.auth_check_func
            )
            for server in authorized_servers:
                server_data = self.mcp_manager.available_tools.get(server)
                tools = (server_data or {}).get("tools", [])
                if not any(getattr(t, "name", None) == "rag_discover_resources" for t in tools):
                    continue

                # Call discovery
                try:
                    raw = await self.mcp_manager.call_tool(
                        server_name=server,
                        tool_name="rag_discover_resources",
                        arguments={"username": username},
                    )
                    structured = self._extract_structured_result(raw)
                    resources = self._extract_resources(structured)
                except Exception as e:
                    logger.warning("Discovery failed for server %s: %s", server, e)
                    resources = []

                # Build UI sources array
                ui_sources: List[Dict[str, Any]] = []
                for r in resources:
                    rid = r.get("id") or r.get("name")
                    if not isinstance(rid, str):
                        continue
                    ui_sources.append({
                        "id": rid,
                        "name": r.get("name") or rid,
                        # New contract: authRequired expected true; pass-through in case of legacy servers
                        "authRequired": bool(r.get("authRequired", True)),
                        # New: include per-resource groups when provided
                        "groups": list(r.get("groups", [])) if isinstance(r.get("groups"), list) else None,
                        "selected": bool(r.get("defaultSelected", False)),
                    })

                # Optional config-driven icon/name
                cfg = (self.mcp_manager.available_tools.get(server) or {}).get("config", {})
                display_name = cfg.get("displayName") or server
                icon = (cfg.get("ui") or {}).get("icon") if isinstance(cfg.get("ui"), dict) else None

                rag_servers.append({
                    "server": server,
                    "displayName": display_name,
                    "icon": icon,
                    "sources": ui_sources,
                })
        except Exception as e:
            logger.error("discover_servers error: %s", e, exc_info=True)

        return rag_servers

    async def search_raw(
        self,
        username: str,
        query: str,
        sources: List[str],
        top_k: int = 8,
        filters: Optional[Dict[str, Any]] = None,
        ranking: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call rag_get_raw_results across servers and merge results.

        sources are server-qualified (server:id). We group by server.
        """
        filters = filters or {}
        ranking = ranking or {}
        by_server: Dict[str, List[str]] = {}
        for s in sources or []:
            if isinstance(s, str) and ":" in s:
                srv, rid = s.split(":", 1)
                by_server.setdefault(srv, []).append(rid)

        all_hits: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {"providers": {}, "top_k": top_k}

        for server, rids in by_server.items():
            try:
                # Check tool availability
                server_data = self.mcp_manager.available_tools.get(server) or {}
                tool_list = server_data.get("tools", [])
                if not any(getattr(t, "name", None) == "rag_get_raw_results" for t in tool_list):
                    continue

                raw = await self.mcp_manager.call_tool(
                    server_name=server,
                    tool_name="rag_get_raw_results",
                    arguments={
                        "username": username,
                        "query": query,
                        "sources": rids,
                        "top_k": top_k,
                        "filters": filters,
                        "ranking": ranking,
                    },
                )
                payload = self._extract_structured_result(raw) or {}
                results = payload.get("results") or {}
                hits = results.get("hits") or []
                # Annotate with server for provenance
                for h in hits:
                    if isinstance(h, dict):
                        h.setdefault("server", server)
                all_hits.extend([h for h in hits if isinstance(h, dict)])
                meta["providers"][server] = {
                    "returned": len(hits),
                    "error": None,
                }
            except Exception as e:
                meta["providers"][server] = {"returned": 0, "error": str(e)}

        # Merge + rerank (simple): sort by score desc if present
        def score_of(h: Dict[str, Any]) -> float:
            try:
                return float(h.get("score", 0.0))
            except Exception:
                return 0.0

        all_hits.sort(key=score_of, reverse=True)
        merged = all_hits[: top_k or len(all_hits)]

        # Prompt-injection risk check on retrieved snippets (observe + log)
        try:
            for h in merged:
                if not isinstance(h, dict):
                    continue
                text = h.get("snippet") or h.get("chunk") or h.get("text") or ""
                if not isinstance(text, str) or not text.strip():
                    continue
                pi = calculate_prompt_injection_risk(text, mode="general")
                if pi.get("risk_level") in ("medium", "high"):
                    log_high_risk_event(
                        source="rag_chunk",
                        user=username,
                        content=text,
                        score=int(pi.get("score", 0)),
                        risk_level=str(pi.get("risk_level")),
                        triggers=list(pi.get("triggers", [])),
                        extra={
                            "server": h.get("server"),
                            "resourceId": h.get("resourceId"),
                        },
                    )
        except Exception:
            logger.debug("Prompt risk check failed (RAG results)", exc_info=True)

        return {
            "results": {
                "hits": merged,
                "stats": {
                    "total_found": len(all_hits),
                    "top_k": top_k,
                },
            },
            "meta_data": meta,
        }

    async def synthesize(
        self,
        username: str,
        query: str,
        sources: List[str],
        top_k: Optional[int] = None,
        synthesis_params: Optional[Dict[str, Any]] = None,
        provided_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call rag_get_synthesized_results across servers when available.

        If not available, fall back to raw search and concatenate snippets.
        """
        synthesis_params = synthesis_params or {}
        provided_context = provided_context or {}

        by_server: Dict[str, List[str]] = {}
        for s in sources or []:
            if isinstance(s, str) and ":" in s:
                srv, rid = s.split(":", 1)
                by_server.setdefault(srv, []).append(rid)

        answers: List[str] = []
        citations: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {"providers": {}}
        used_fallback = False

        for server, rids in by_server.items():
            try:
                server_data = self.mcp_manager.available_tools.get(server) or {}
                tool_list = server_data.get("tools", [])
                has_synth = any(getattr(t, "name", None) == "rag_get_synthesized_results" for t in tool_list)
                if has_synth:
                    raw = await self.mcp_manager.call_tool(
                        server_name=server,
                        tool_name="rag_get_synthesized_results",
                        arguments={
                            "username": username,
                            "query": query,
                            "sources": rids,
                            **({"top_k": top_k} if top_k is not None else {}),
                            "synthesis_params": synthesis_params,
                            "provided_context": provided_context,
                        },
                    )
                    payload = self._extract_structured_result(raw) or {}
                    results = payload.get("results") or {}
                    ans = results.get("answer")
                    if isinstance(ans, str) and ans:
                        answers.append(ans)
                    cits = results.get("citations") or []
                    if isinstance(cits, list):
                        for c in cits:
                            if isinstance(c, dict):
                                c.setdefault("server", server)
                        citations.extend([c for c in cits if isinstance(c, dict)])
                    meta["providers"][server] = {"used_synth": True, "error": None}
                else:
                    used_fallback = True
                    raw_payload = await self.search_raw(username, query, [f"{server}:{rid}" for rid in rids], top_k=top_k or 8)
                    # Build a rudimentary answer from snippets
                    hits = ((raw_payload.get("results") or {}).get("hits") or [])
                    snippet_texts = [h.get("snippet") or h.get("chunk") or "" for h in hits if isinstance(h, dict)]
                    if snippet_texts:
                        answers.append("\n\n".join(snippet_texts[:3]))
                    meta["providers"][server] = {"used_synth": False, "error": None}
            except Exception as e:
                meta["providers"][server] = {"used_synth": False, "error": str(e)}

        final_answer = "\n\n---\n\n".join([a for a in answers if a]) if answers else ""
        return {
            "results": {
                "answer": final_answer,
                "citations": citations or None,
                "limits": {"truncated": False} if final_answer else None,
            },
            "meta_data": {**meta, "fallback_used": used_fallback},
        }

    # --- helpers ---------------------------------------------------------
    def _extract_structured_result(self, raw: Any) -> Dict[str, Any]:
        """Best-effort extraction of a structured payload from FastMCP result."""
        try:
            # Preferred attributes from fastmcp
            if hasattr(raw, "structured_content") and raw.structured_content:
                if isinstance(raw.structured_content, dict):
                    return raw.structured_content
            if hasattr(raw, "data") and raw.data:
                if isinstance(raw.data, dict):
                    return raw.data
            if hasattr(raw, "content") and raw.content:
                contents = getattr(raw, "content")
                # content is typically a list of segments with .text
                if isinstance(contents, list) and contents:
                    first = contents[0]
                    text = getattr(first, "text", None)
                    if isinstance(text, str) and text.strip():
                        import json
                        try:
                            obj = json.loads(text)
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            # Not JSON; ignore
                            pass
        except Exception as parse_err:  # pragma: no cover - defensive
            logger.debug("Non-fatal: failed to parse structured result: %s", parse_err)
        return {}

    def _extract_resources(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract list of resource dicts from a normalized tool result."""
        if not isinstance(payload, dict):
            return []
        results = payload.get("results") if isinstance(payload.get("results"), dict) else payload
        # Support both {results: {resources: [...]}} and {resources: [...]}
        resources = (
            (results.get("resources") if isinstance(results, dict) else None)
            or payload.get("resources")
            or []
        )
        if isinstance(resources, list):
            # ensure each entry is a dict
            return [r for r in resources if isinstance(r, dict)]
        return []


__all__ = ["RAGMCPService"]
