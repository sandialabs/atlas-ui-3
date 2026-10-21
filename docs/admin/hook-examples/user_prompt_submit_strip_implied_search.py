#!/usr/bin/env python3
"""UserPromptSubmit example: keep the implied search tool out of outbound turns.

Event:    UserPromptSubmit (ChatOrchestrator.execute, after the user message is
          stored, before file ingestion and mode dispatch).
Matcher:  none -- this event supplies no matcher value.
Payload:  {"prompt": str, "selected_tools": [str], "selected_data_sources": [str],
            "agent_mode": bool}
Can do:   modify (narrow the turn's tool selection) | deny | continue.
on_error: allow (per-event default).

Why: selecting data sources implies the built-in ``atlas_search`` tool
(issue #1102), so a turn that carries a source can also call an outbound
MCP tool -- a web fetch, a mail client, anything that leaves the
deployment. An internal corpus and an outbound tool in the same turn is a
prompt-injection path that no per-user access control narrows.

What it does: when the turn carries a tool from an MCP server (anything that
is not one of the built-in ``atlas_`` tools), the implied search tool is
removed from the list. The sources are still the user's -- the turn simply
does not search them while an outbound tool is in play.

Narrowing only: the orchestrator intersects the returned list with what the
turn would really offer, so a hook can never grant access to a tool the
user did not choose. An empty list means "none" and is preserved.
"""
import json
import sys

BUILTIN_TOOLS = {
    "atlas_canvas",
    "atlas_sleep",
    "atlas_search",
    "atlas_discover_sources",
    "atlas_discover_launch_options",
    "atlas_launch",
    "atlas_get_runs",
    "atlas_result",
    "atlas_follow_up",
}
# The pre-#855 names of the built-ins (issue #855). A selection may still
# carry them, and they are the same tools.
LEGACY_ALIASES = {
    "canvas_canvas": "atlas_canvas",
    "atlas_agent_sleep": "atlas_sleep",
    "atlas_rag_query": "atlas_search",
    "atlas_rag_discover_data_sources": "atlas_discover_sources",
}
# The search tool, under its current name and the one it had before the tools
# were consolidated.
SEARCH_TOOLS = ("atlas_search", "atlas_rag_query")


def normalize(tool):
    """The current name of a built-in tool, whatever it is called."""
    return LEGACY_ALIASES.get(tool, tool)


def is_search_tool(tool):
    return tool in SEARCH_TOOLS


def is_builtin(tool):
    # An explicit list, not a prefix: an operator may configure an MCP
    # server whose name starts with "atlas_", and that server is outbound.
    return normalize(tool) in BUILTIN_TOOLS


env = json.load(sys.stdin)
payload = env.get("payload") or {}
tools = payload.get("selected_tools") or []

# Nothing outbound in the turn: the search tool stays.
if not any(not is_builtin(tool) for tool in tools):
    sys.exit(0)

# The search tool is not in the turn: nothing to do.
if not any(is_search_tool(tool) for tool in tools):
    sys.exit(0)

kept = [tool for tool in tools if not is_search_tool(tool)]
print(json.dumps({
    "decision": "modify",
    "payload": {"selected_tools": kept},
}))
sys.exit(0)
