"""Resolve the tools a turn actually offers the model (issue #1102).

Picking a data source and ticking the built-in search tool used to be two
separate mechanisms, and a user who did only the first got a turn that read
nothing -- plus a warning about it. The two are now one: a selection of data
sources *implies* ``atlas_search``, so the model is *offered* the tool and
decides whether to call it (availability is not a forced invocation).

The implication is a property of the turn, not a setting:

- there is no opt-out and nothing new is persisted, so a deployment cannot
  end up with a half-configured state where the sources are selected but the
  tool is not;
- deselecting every source removes the implication again, and an *explicit*
  selection of the search tool survives either way -- the user's own
  selection is never taken away from them;
- the tool only appears when the deployment actually offers it
  (``FEATURE_RAG_ENABLED`` and ``FEATURE_ATLAS_RAG_TOOLS_ENABLED``), so the
  feature flag keeps gating the schema. The same two flags gate the schema
  builder, the tool ACL and the execution path, read from one place
  (``atlas_server.search_tools_enabled``).

Nothing here widens what a search may read. The data source selection stays
the ceiling on what ``atlas_search`` can reach, the user's own access is
intersected with it at query time, and the per-source classification checks
run on every query -- all of that is enforced further down, in the tool
executor and the RAG services.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from atlas.modules.mcp_tools.atlas_server import (
    SEARCH_TOOL_NAME,
    normalize_tool_name,
    search_tools_enabled,
)

logger = logging.getLogger(__name__)


def has_search_tool(selected_tools: Optional[Sequence[str]]) -> bool:
    """Whether the tool list already names the built-in search tool.

    Recognizes the pre-#855 spelling (``atlas_rag_query``) too: a saved
    conversation or a stale browser selection may still carry it, and it
    resolves to the same tool.
    """
    return any(
        normalize_tool_name(t) == SEARCH_TOOL_NAME
        for t in (selected_tools or [])
        if isinstance(t, str)
    )


def resolve_effective_tools(
    selected_tools: Optional[Sequence[str]],
    selected_data_sources: Optional[Sequence[str]],
    app_settings: Any = None,
    *,
    imply: bool = True,
) -> List[str]:
    """The user's tool selection plus the built-in search tool, if sources are selected.

    A no-op when no data sources were selected, when the search tool is
    already in the list (under either its current or its pre-#855 name), or
    when the RAG feature flags are off.

    ``imply=False`` keeps the user's selection exactly as given. The
    orchestrator uses it when the turn must not gain a tool it did not
    arrive with: a model that cannot call tools, or a policy hook that has
    narrowed the tool list (a hook gets the last word over the whole list,
    implied tool included).
    """
    tools = [t for t in (selected_tools or []) if isinstance(t, str)]
    if not imply:
        return tools
    if not selected_data_sources:
        return tools
    if has_search_tool(tools):
        return tools
    if not search_tools_enabled(app_settings):
        return tools
    logger.debug(
        "Adding %s to the turn's tools: %d data source(s) selected",
        SEARCH_TOOL_NAME,
        len(selected_data_sources),
    )
    return tools + [SEARCH_TOOL_NAME]
