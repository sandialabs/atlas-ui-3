"""Server-side data-classification check for a whole chat turn (issue #1032).

The active conversation classification is the authority. Every component a
turn would send conversation data to -- the selected model, the MCP servers
behind the selected tools, and the selected RAG sources -- must explicitly list
that classification in its ``allowed_data_classifications`` (a legacy
``compliance_level`` counts as a one-element list). A component that declares
nothing is approved for no classified session.

The frontend hides components the rule excludes, but a stale bundle, the CLI or
a hand-crafted client can still name them, so the turn is checked here before
anything runs.
"""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

from atlas.core.compliance import ComplianceLevelManager, declared_classifications
from atlas.modules.config.models import lookup_model_config
from atlas.modules.mcp_tools.atlas_server import is_atlas_tool, normalize_tool_name

logger = logging.getLogger(__name__)


def _server_for_tool(tool: str, server_names: Iterable[str]) -> Optional[str]:
    """The configured server a ``server_tool`` name belongs to (longest prefix)."""
    best: Optional[str] = None
    for name in server_names:
        if tool.startswith(f"{name}_") and (best is None or len(name) > len(best)):
            best = name
    return best


def _resolve_tool_server(tool: str, tool_manager: Any, server_names: Iterable[str]) -> Optional[str]:
    """The server a tool belongs to, as the executor resolves it.

    The tool manager's discovery index is authoritative (server names can
    contain underscores, so a prefix can be ambiguous); the longest configured
    prefix is the fallback for a server whose tools are not discovered yet.
    """
    lookup = getattr(tool_manager, "get_server_for_tool", None)
    if callable(lookup):
        try:
            server = lookup(tool)
        except Exception:
            server = None
        if isinstance(server, str) and server in server_names:
            return server
    return _server_for_tool(tool, server_names)


def _mcp_servers(tool_manager: Any, config_manager: Any) -> Dict[str, Any]:
    servers = getattr(tool_manager, "servers_config", None)
    if isinstance(servers, dict) and servers:
        return servers
    try:
        return dict(config_manager.mcp_config.servers)
    except Exception:
        return {}


def _rag_sources(config_manager: Any) -> Dict[str, Any]:
    try:
        return dict(config_manager.rag_sources_config.sources)
    except Exception:
        return {}


def find_classification_violations(
    compliance_mgr: ComplianceLevelManager,
    active_level: Optional[str],
    *,
    model: str,
    config_manager: Any,
    tool_manager: Any = None,
    selected_tools: Optional[List[str]] = None,
    selected_data_sources: Optional[List[str]] = None,
) -> List[str]:
    """Human-readable reasons the turn may not run at ``active_level``.

    Empty when every component the turn names is approved for the active
    classification, or when no classification is active.

    A non-built-in tool that cannot be tied to a configured MCP server is
    reported (fail closed). Data sources whose server is not configured are
    not reported: the query path refuses them on its own.
    """
    if not active_level:
        return []
    violations: List[str] = []

    model_config = lookup_model_config(config_manager.llm_config, model)
    if model_config is None or not compliance_mgr.classification_permits(
        active_level, declared_classifications(model_config)
    ):
        violations.append(f"the selected model ({model})")

    if selected_tools:
        servers = _mcp_servers(tool_manager, config_manager)
        denied_servers: List[str] = []
        unknown_tools: List[str] = []
        for tool in selected_tools:
            if not isinstance(tool, str):
                continue
            # Built-in ``atlas`` tools run in-process; atlas_search reads only
            # sources that are checked on their own at query time.
            if is_atlas_tool(normalize_tool_name(tool)):
                continue
            server = _resolve_tool_server(tool, tool_manager, servers.keys())
            if server is None:
                if tool not in unknown_tools:
                    unknown_tools.append(tool)
                continue
            if server in denied_servers:
                continue
            if not compliance_mgr.classification_permits(
                active_level, declared_classifications(servers[server])
            ):
                denied_servers.append(server)
        violations.extend(f"tool server {name}" for name in denied_servers)
        violations.extend(f"tool {name} (no known server)" for name in unknown_tools)

    if selected_data_sources:
        sources = _rag_sources(config_manager)
        denied_sources: List[str] = []
        for qualified in selected_data_sources:
            if not isinstance(qualified, str):
                continue
            server = qualified.split(":", 1)[0]
            if server not in sources or server in denied_sources:
                continue
            if not compliance_mgr.classification_permits(
                active_level, declared_classifications(sources[server])
            ):
                denied_sources.append(server)
        violations.extend(f"data source {name}" for name in denied_sources)

    return violations


async def find_unapproved_corpora(
    active_level: Optional[str],
    user_email: Optional[str],
    selected_data_sources: Optional[List[str]],
    *,
    unified_rag: Any = None,
    rag_mcp: Any = None,
    config_manager: Any = None,
) -> Tuple[List[str], List[str]]:
    """Selected ``server:corpus`` keys discovery does not offer at ``active_level``.

    A corpus can declare narrower classifications than its server, and only
    the RAG backend knows them. Discovery run with the active level already
    drops unapproved servers and corpora (it is the same allow-list the
    ``atlas_search`` tool is bounded by), so a selected corpus missing from it
    is refused rather than queried.

    Returns ``(unapproved, unverified)``: corpora discovery answered for and
    did not offer, and corpora whose server's discovery failed or returned
    nothing, which cannot be judged either way. Only the selected servers are
    asked, concurrently. Sources whose server is not configured are not
    reported; the query path refuses those on its own.
    """
    if not active_level or not selected_data_sources:
        return [], []
    configured = _rag_sources(config_manager)
    wanted = [
        s for s in selected_data_sources
        if isinstance(s, str) and ":" in s and s.split(":", 1)[0] in configured
    ]
    if not wanted:
        return [], []
    servers = sorted({s.split(":", 1)[0] for s in wanted})
    http = [n for n in servers if getattr(configured[n], "type", None) == "http"]
    mcp = [n for n in servers if n not in http]

    lookups = []
    if http and unified_rag is not None:
        lookups.append(unified_rag.discover_data_sources(
            user_email, user_compliance_level=active_level, only_servers=http
        ))
    if mcp and rag_mcp is not None:
        lookups.append(rag_mcp.discover_servers(
            user_email, user_compliance_level=active_level, only_servers=mcp
        ))
    discovered: List[Dict[str, Any]] = []
    for result in await asyncio.gather(*lookups):
        discovered.extend(result or [])

    answered = {
        server.get("server")
        for server in discovered
        if not server.get("discoveryFailed")
    }
    offered = {
        f"{server.get('server')}:{source.get('id')}"
        for server in discovered
        for source in server.get("sources", []) or []
    }
    unapproved: List[str] = []
    unverified: List[str] = []
    for key in wanted:
        if key in offered:
            continue
        if key.split(":", 1)[0] in answered:
            unapproved.append(key)
        else:
            unverified.append(key)
    return unapproved, unverified
