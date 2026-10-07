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

import logging
from typing import Any, Dict, Iterable, List, Optional

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

    Tool names that match no configured MCP server, and data sources whose
    server is not configured, are not reported: they reach no component, and
    the authorization and query paths already refuse them.
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
        for tool in selected_tools:
            if not isinstance(tool, str):
                continue
            # Built-in ``atlas`` tools run in-process; atlas_search reads only
            # sources that are checked on their own at query time.
            if is_atlas_tool(normalize_tool_name(tool)):
                continue
            server = _server_for_tool(tool, servers.keys())
            if server is None or server in denied_servers:
                continue
            if not compliance_mgr.classification_permits(
                active_level, declared_classifications(servers[server])
            ):
                denied_servers.append(server)
        violations.extend(f"tool server {name}" for name in denied_servers)

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
