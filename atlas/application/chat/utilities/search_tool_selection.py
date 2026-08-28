"""Make ``atlas_search`` reachable whenever the user selected data sources.

RAG used to run itself: if a turn carried ``data_sources``, the LLM callers
queried every source *before* the model was asked anything and injected the
passages as a system message. The model never decided to search, the user
never saw a search happen, and the query was whatever the conversation
happened to look like.

Search is now an ordinary tool call -- the model calls ``atlas_search`` with a
query it chose, and the passages come back as a visible tool result. The data
source selection keeps its meaning (it is the *ceiling* on what that tool may
read, enforced in ``mcp_execution``), it just no longer triggers anything on
its own.

Which leaves one gap: a user who picks a data source but never ticks the
search tool. Under the old behaviour that turn had RAG; under a literal
reading of the new one it silently has nothing at all. So a selection of data
sources implies the search tool, exactly as if the user had ticked it -- the
model still has to decide to call it.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from atlas.modules.mcp_tools.atlas_server import SEARCH_TOOL_NAME, normalize_tool_name

logger = logging.getLogger(__name__)


def search_tools_enabled(config_manager: Any) -> bool:
    """True when both RAG and the built-in ATLAS RAG tools are turned on."""
    settings = getattr(config_manager, "app_settings", None)
    if settings is None:
        return False
    return bool(
        getattr(settings, "feature_rag_enabled", False)
        and getattr(settings, "feature_atlas_rag_tools_enabled", False)
    )


def with_search_tool(
    selected_tools: Optional[Sequence[str]],
    data_sources: Optional[Sequence[str]],
    config_manager: Any,
) -> List[str]:
    """Return ``selected_tools`` plus ``atlas_search`` when sources are selected.

    A no-op when no data sources were selected, when the search tool is already
    in the list (under either its current or its pre-#855 name), or when the
    RAG feature flags are off.
    """
    tools = [t for t in (selected_tools or []) if isinstance(t, str)]
    if not data_sources:
        return tools
    if any(normalize_tool_name(t) == SEARCH_TOOL_NAME for t in tools):
        return tools
    if not search_tools_enabled(config_manager):
        return tools
    logger.debug(
        "Adding %s to the tool schema: %d data source(s) selected",
        SEARCH_TOOL_NAME,
        len(data_sources),
    )
    return tools + [SEARCH_TOOL_NAME]
