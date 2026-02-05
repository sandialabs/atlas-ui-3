"""RAG MCP Aggregator Service (Phase 1: Discovery)

Aggregates discovery of RAG resources from authorized MCP servers that expose
the `rag_discover_resources` tool. Returns a flat list of data source IDs for
backward-compatible UI, with server-qualified IDs to avoid collisions.

Future phases will add search/synthesis and richer shapes.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional


from core.compliance import get_compliance_manager
from core.prompt_risk import calculate_prompt_injection_risk, log_high_risk_event
from core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)


class RAGMCPService:
    """Aggregator for RAG over MCP servers."""

    def __init__(self, mcp_manager, config_manager, auth_check_func) -> None:
        self.mcp_manager = mcp_manager
        self.config_manager = config_manager
        self.auth_check_func = auth_check_func

    async def _get_authorized_rag_servers(self, username: str, rag_servers: dict) -> List[str]:
        """Get list of RAG servers the user is authorized to access.

        This checks authorization directly against rag_mcp_config servers,
        independent of mcp_manager.servers_config (which excludes RAG servers
        to keep them separate from the tools panel).
        """
        authorized = []
        for server_name, server_config in rag_servers.items():
            if not server_config.enabled:
                continue

            required_groups = server_config.groups or []
            if not required_groups:
                # No group restriction - available to all
                authorized.append(server_name)
                continue

            # Check if user is in any of the required groups
            group_checks = [
                await self.auth_check_func(username, group)
                for group in required_groups
            ]
            if any(group_checks):
                authorized.append(server_name)

        return authorized

    async def discover_data_sources(self, username: str, user_compliance_level: Optional[str] = None) -> List[str]:
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
            # Determine RAG servers current user can see
            # Use rag_mcp_config directly since servers_config was restored above
            rag_servers = self.config_manager.rag_mcp_config.servers
            authorized_servers: List[str] = await self._get_authorized_rag_servers(
                username, rag_servers
            )

            if not authorized_servers:
                logger.info("No authorized MCP servers for user %s", sanitize_for_logging(username))
                return []

            # --- Compliance Filtering (Step 2) ---
            if user_compliance_level:
                compliance_mgr = get_compliance_manager()
                filtered_servers = []
                for server in authorized_servers:
                    cfg = (self.mcp_manager.available_tools.get(server) or {}).get("config", {})
                    server_compliance_level = cfg.get("compliance_level")
                    if compliance_mgr.is_accessible(
                        user_level=user_compliance_level, resource_level=server_compliance_level
                    ):
                        filtered_servers.append(server)
                    else:
                        logger.info(
                            "Skipping RAG server %s due to compliance level mismatch (user: %s, server: %s)",
                            sanitize_for_logging(server),
                            sanitize_for_logging(user_compliance_level),
                            sanitize_for_logging(server_compliance_level),
                        )
                authorized_servers = filtered_servers
                if not authorized_servers:
                    logger.info("No authorized MCP servers remain after compliance filtering for user %s", sanitize_for_logging(username))
                    return []
            # -------------------------------------

            # Filter to servers that advertise the discovery tool
            servers_with_discovery: List[str] = []
            for server in authorized_servers:
                server_data = self.mcp_manager.available_tools.get(server)
                tool_list = (server_data or {}).get("tools", [])
                if any(getattr(t, "name", None) == "rag_discover_resources" for t in tool_list):
                    servers_with_discovery.append(server)

            if not servers_with_discovery:
                logger.info("No servers implement rag_discover_resources for user %s", sanitize_for_logging(username))
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
                        sanitize_for_logging(server),
                        sanitize_for_logging(username),
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

    async def discover_servers(self, username: str, user_compliance_level: Optional[str] = None) -> List[Dict[str, Any]]:
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
            compliance_mgr = get_compliance_manager() if user_compliance_level else None

            # Use rag_mcp_config directly since servers_config was restored above
            rag_cfg_servers = self.config_manager.rag_mcp_config.servers
            authorized_servers: List[str] = await self._get_authorized_rag_servers(
                username, rag_cfg_servers
            )

            # --- Compliance Filtering (Step 2) ---
            if compliance_mgr:
                filtered_servers = []
                for server in authorized_servers:
                    cfg = (self.mcp_manager.available_tools.get(server) or {}).get("config", {})
                    server_compliance_level = cfg.get("compliance_level")
                    if compliance_mgr.is_accessible(
                        user_level=user_compliance_level, resource_level=server_compliance_level
                    ):
                        filtered_servers.append(server)
                    else:
                        logger.info(
                            "Skipping RAG server %s due to compliance level mismatch (user: %s, server: %s)",
                            sanitize_for_logging(server),
                            sanitize_for_logging(user_compliance_level),
                            sanitize_for_logging(server_compliance_level),
                        )
                authorized_servers = filtered_servers
            # -------------------------------------

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

                    # --- Compliance Filtering (Step 3) ---
                    # Check for both camelCase (MCP standard) and snake_case (RAG mock standard)
                    resource_compliance_level = r.get("complianceLevel") or r.get("compliance_level")
                    if compliance_mgr and not compliance_mgr.is_accessible(
                        user_level=user_compliance_level, resource_level=resource_compliance_level
                    ):
                        logger.info(
                            "Skipping RAG resource %s:%s due to compliance level mismatch (user: %s, resource: %s)",
                            sanitize_for_logging(server),
                            sanitize_for_logging(rid),
                            sanitize_for_logging(user_compliance_level),
                            sanitize_for_logging(resource_compliance_level),
                        )
                        continue
                    # -------------------------------------

                    ui_sources.append({
                        "id": rid,
                        "name": r.get("name") or rid,
                        # New contract: authRequired expected true; pass-through in case of legacy servers
                        "authRequired": bool(r.get("authRequired", True)),
                        # New: include per-resource groups when provided
                        "groups": list(r.get("groups", [])) if isinstance(r.get("groups"), list) else None,
                        "selected": bool(r.get("defaultSelected", False)),
                        # Include compliance_level from resource or inherit from server
                        "complianceLevel": resource_compliance_level if resource_compliance_level else None,
                    })

                # Optional config-driven icon/name and compliance level
                cfg = (self.mcp_manager.available_tools.get(server) or {}).get("config", {})
                display_name = cfg.get("displayName") or server
                icon = (cfg.get("ui") or {}).get("icon") if isinstance(cfg.get("ui"), dict) else None
                compliance_level = cfg.get("compliance_level")

                rag_servers.append({
                    "server": server,
                    "displayName": display_name,
                    "icon": icon,
                    "complianceLevel": compliance_level,
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
        logger.debug(
            "[MCP-RAG] search_raw called: user=%s, query_preview=%s..., sources=%s, top_k=%d",
            sanitize_for_logging(username),
            sanitize_for_logging(query[:100]) if query else "(empty)",
            sources,
            top_k,
        )

        filters = filters or {}
        ranking = ranking or {}
        by_server: Dict[str, List[str]] = {}
        for s in sources or []:
            if isinstance(s, str) and ":" in s:
                srv, rid = s.split(":", 1)
                by_server.setdefault(srv, []).append(rid)

        logger.debug("[MCP-RAG] search_raw sources grouped by server: %s", by_server)

        all_hits: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {"providers": {}, "top_k": top_k}

        for server, rids in by_server.items():
            logger.debug("[MCP-RAG] search_raw processing server=%s, resource_ids=%s", server, rids)
            try:
                # Check tool availability
                server_data = self.mcp_manager.available_tools.get(server) or {}
                tool_list = server_data.get("tools", [])
                if not any(getattr(t, "name", None) == "rag_get_raw_results" for t in tool_list):
                    logger.debug("[MCP-RAG] Server %s lacks rag_get_raw_results tool, skipping", server)
                    continue

                logger.debug("[MCP-RAG] Calling rag_get_raw_results on server %s", server)
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
                logger.debug("[MCP-RAG] Server %s raw response type: %s", server, type(raw).__name__)

                payload = self._extract_structured_result(raw) or {}
                results = payload.get("results") or {}
                hits = results.get("hits") or []
                logger.debug("[MCP-RAG] Server %s returned %d hits", server, len(hits))

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
                logger.error("[MCP-RAG] Server %s search_raw error: %s", server, e, exc_info=True)
                meta["providers"][server] = {"returned": 0, "error": str(e)}

        # Merge + rerank (simple): sort by score desc if present
        def score_of(h: Dict[str, Any]) -> float:
            try:
                return float(h.get("score", 0.0))
            except Exception:
                return 0.0

        all_hits.sort(key=score_of, reverse=True)
        merged = all_hits[: top_k or len(all_hits)]

        logger.info(
            "[MCP-RAG] search_raw complete: total_hits=%d, merged_count=%d, providers=%s",
            len(all_hits),
            len(merged),
            list(meta["providers"].keys()),
        )

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
        logger.debug(
            "[MCP-RAG] synthesize called: user=%s, query_preview=%s..., sources=%s, top_k=%s",
            sanitize_for_logging(username),
            sanitize_for_logging(query[:100]) if query else "(empty)",
            sources,
            top_k,
        )

        synthesis_params = synthesis_params or {}
        provided_context = provided_context or {}

        by_server: Dict[str, List[str]] = {}
        for s in sources or []:
            if isinstance(s, str) and ":" in s:
                srv, rid = s.split(":", 1)
                by_server.setdefault(srv, []).append(rid)

        logger.debug("[MCP-RAG] Sources grouped by server: %s", by_server)

        answers: List[str] = []
        citations: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {"providers": {}}
        used_fallback = False

        for server, rids in by_server.items():
            logger.debug(
                "[MCP-RAG] Processing server=%s, resource_ids=%s",
                server,
                rids,
            )
            try:
                server_data = self.mcp_manager.available_tools.get(server) or {}
                tool_list = server_data.get("tools", [])
                tool_names = [getattr(t, "name", None) for t in tool_list]
                logger.debug("[MCP-RAG] Server %s available tools: %s", server, tool_names)

                has_synth = any(getattr(t, "name", None) == "rag_get_synthesized_results" for t in tool_list)
                if has_synth:
                    logger.debug("[MCP-RAG] Server %s has rag_get_synthesized_results, calling...", server)
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
                    logger.debug("[MCP-RAG] Server %s raw response type: %s", server, type(raw).__name__)

                    payload = self._extract_structured_result(raw) or {}
                    logger.debug("[MCP-RAG] Server %s extracted payload keys: %s", server, list(payload.keys()))
                    logger.debug("[MCP-RAG] Server %s full payload: %s", server, sanitize_for_logging(str(payload)[:1000]))

                    results = payload.get("results") or {}
                    logger.debug("[MCP-RAG] Server %s results type: %s, results keys: %s", server, type(results).__name__, list(results.keys()) if isinstance(results, dict) else "N/A")
                    logger.debug("[MCP-RAG] Server %s results content: %s", server, sanitize_for_logging(str(results)[:500]))

                    ans = results.get("answer")
                    if isinstance(ans, str) and ans:
                        logger.debug(
                            "[MCP-RAG] Server %s answer length=%d, preview=%s...",
                            server,
                            len(ans),
                            sanitize_for_logging(ans[:200]),
                        )
                        answers.append(ans)
                    cits = results.get("citations") or []
                    if isinstance(cits, list):
                        for c in cits:
                            if isinstance(c, dict):
                                c.setdefault("server", server)
                        citations.extend([c for c in cits if isinstance(c, dict)])
                    meta["providers"][server] = {"used_synth": True, "error": None}
                    logger.info("[MCP-RAG] Server %s synthesis complete: answer_length=%d", server, len(ans) if ans else 0)
                else:
                    logger.debug("[MCP-RAG] Server %s lacks rag_get_synthesized_results, using fallback search_raw", server)
                    used_fallback = True
                    raw_payload = await self.search_raw(username, query, [f"{server}:{rid}" for rid in rids], top_k=top_k or 8)
                    # Build a rudimentary answer from snippets
                    hits = ((raw_payload.get("results") or {}).get("hits") or [])
                    logger.debug("[MCP-RAG] Server %s fallback search returned %d hits", server, len(hits))
                    snippet_texts = [h.get("snippet") or h.get("chunk") or "" for h in hits if isinstance(h, dict)]
                    if snippet_texts:
                        answers.append("\n\n".join(snippet_texts[:3]))
                    meta["providers"][server] = {"used_synth": False, "error": None}
            except Exception as e:
                logger.error("[MCP-RAG] Server %s synthesis error: %s", server, e, exc_info=True)
                meta["providers"][server] = {"used_synth": False, "error": str(e)}

        final_answer = "\n\n---\n\n".join([a for a in answers if a]) if answers else ""
        logger.info(
            "[MCP-RAG] synthesize complete: total_answers=%d, final_answer_length=%d, used_fallback=%s",
            len(answers),
            len(final_answer),
            used_fallback,
        )

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
        import json

        logger.debug("[MCP-RAG] _extract_structured_result: raw type=%s", type(raw).__name__)

        try:
            # Log available attributes for debugging
            if hasattr(raw, "__dict__"):
                logger.debug("[MCP-RAG] _extract_structured_result: raw attributes=%s", list(raw.__dict__.keys()) if hasattr(raw, "__dict__") else "N/A")

            # If raw is already a dict, return it directly
            if isinstance(raw, dict):
                logger.debug("[MCP-RAG] _extract_structured_result: raw is already a dict with keys=%s", list(raw.keys()))
                return raw

            # Preferred attributes from fastmcp
            if hasattr(raw, "structured_content") and raw.structured_content:
                logger.debug("[MCP-RAG] _extract_structured_result: found structured_content")
                if isinstance(raw.structured_content, dict):
                    return raw.structured_content
            if hasattr(raw, "data") and raw.data:
                logger.debug("[MCP-RAG] _extract_structured_result: found data")
                if isinstance(raw.data, dict):
                    return raw.data

            if hasattr(raw, "content") and raw.content:
                contents = getattr(raw, "content")
                logger.debug("[MCP-RAG] _extract_structured_result: found content, type=%s, len=%s", type(contents).__name__, len(contents) if hasattr(contents, '__len__') else "N/A")

                # content is typically a list of segments with .text
                if isinstance(contents, list) and contents:
                    # Try all content items, not just the first
                    for idx, item in enumerate(contents):
                        logger.debug("[MCP-RAG] _extract_structured_result: content[%d] type=%s", idx, type(item).__name__)

                        # Try .text attribute
                        text = getattr(item, "text", None)
                        if text is None and isinstance(item, dict):
                            text = item.get("text")

                        if text:
                            logger.debug("[MCP-RAG] _extract_structured_result: text type=%s, preview=%s", type(text).__name__, sanitize_for_logging(str(text)[:300]))
                            if isinstance(text, str) and text.strip():
                                try:
                                    obj = json.loads(text)
                                    if isinstance(obj, dict):
                                        logger.debug("[MCP-RAG] _extract_structured_result: parsed JSON with keys=%s", list(obj.keys()))
                                        return obj
                                except Exception as json_err:
                                    logger.debug("[MCP-RAG] _extract_structured_result: JSON parse failed for content[%d]: %s", idx, json_err)

                        # Try if item is itself a dict with results/meta_data
                        if isinstance(item, dict):
                            if "results" in item or "meta_data" in item:
                                logger.debug("[MCP-RAG] _extract_structured_result: content[%d] is a dict with results/meta_data", idx)
                                return item

                # If content is a single string (not list), try to parse as JSON
                elif isinstance(contents, str) and contents.strip():
                    logger.debug("[MCP-RAG] _extract_structured_result: content is a string, trying JSON parse")
                    try:
                        obj = json.loads(contents)
                        if isinstance(obj, dict):
                            logger.debug("[MCP-RAG] _extract_structured_result: parsed content string as JSON with keys=%s", list(obj.keys()))
                            return obj
                    except Exception as json_err:
                        logger.debug("[MCP-RAG] _extract_structured_result: JSON parse of content string failed: %s", json_err)

            # Try to convert raw to string and parse as JSON (last resort)
            if hasattr(raw, "__str__"):
                raw_str = str(raw)
                # Only try if it looks like JSON
                if raw_str.strip().startswith("{"):
                    logger.debug("[MCP-RAG] _extract_structured_result: trying __str__ as JSON: %s...", sanitize_for_logging(raw_str[:200]))
                    try:
                        obj = json.loads(raw_str)
                        if isinstance(obj, dict):
                            logger.debug("[MCP-RAG] _extract_structured_result: parsed __str__ as JSON with keys=%s", list(obj.keys()))
                            return obj
                    except Exception:
                        pass

        except Exception as parse_err:  # pragma: no cover - defensive
            logger.debug("Non-fatal: failed to parse structured result: %s", parse_err)

        logger.debug("[MCP-RAG] _extract_structured_result: returning empty dict")
        return {}

    def _extract_resources(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract list of resource dicts from a normalized tool result."""
        if not isinstance(payload, dict):
            return []
        results = payload.get("results") if isinstance(payload.get("results"), dict) else payload
        # Support both {results: {resources: [...]}} and {results: [...]}
        # Also support the RAG mock format: {accessible_data_sources: [...]}
        resources = (
            (results.get("resources") if isinstance(results, dict) else None)
            or payload.get("resources")
            or payload.get("accessible_data_sources") # Added support for RAG mock format
            or []
        )
        if isinstance(resources, list):
            # ensure each entry is a dict
            return [r for r in resources if isinstance(r, dict)]
        return []


__all__ = ["RAGMCPService"]
