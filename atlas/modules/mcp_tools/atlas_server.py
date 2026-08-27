"""The consolidated ``atlas`` built-in tool server (issue #855).

ATLAS grew three built-in, non-MCP "pseudo-servers" one at a time: ``canvas``
(render final content in the side panel), ``atlas_agent`` (wait between agent
steps) and ``atlas_rag`` (query retrieval sources). Each showed up in the tools
panel as its own server with one or two tools, which made a short list of
built-ins look like a crowd of servers.

They are now a single server, ``atlas``, exposing ``atlas_canvas``,
``atlas_sleep`` and ``atlas_search``. The old fully-qualified names are still
accepted -- persisted tool selections, saved conversations and non-UI clients
all carry them -- and are normalized to the new names at the edges via
``normalize_tool_name``.

``atlas_search`` deliberately takes a single ``query`` argument. The data
sources it reads are the ones selected in the UI (falling back to everything
the user is authorized for when nothing is selected), so the model cannot pick
its own sources and there is no separate "discover sources" step to run first.
"""

from typing import Any, Dict, List, Optional

ATLAS_SERVER_NAME = "atlas"

CANVAS_TOOL_NAME = "atlas_canvas"
SLEEP_TOOL_NAME = "atlas_sleep"
SEARCH_TOOL_NAME = "atlas_search"

ATLAS_TOOL_NAMES = (CANVAS_TOOL_NAME, SLEEP_TOOL_NAME, SEARCH_TOOL_NAME)

# Pre-#855 fully-qualified names -> consolidated names. ``atlas_rag_discover_
# data_sources`` is intentionally absent: search now uses the UI selection, so
# there is nothing for a discover step to feed. It keeps executing (old
# conversations may replay it) but is no longer advertised.
LEGACY_TOOL_ALIASES = {
    "canvas_canvas": CANVAS_TOOL_NAME,
    "atlas_agent_sleep": SLEEP_TOOL_NAME,
    "atlas_rag_query": SEARCH_TOOL_NAME,
}

# Consolidated name -> the pre-#855 name(s) that resolved to it. Used where a
# *value* has to be recognized under its old spelling too -- most importantly
# hook matchers, which are operator-written regexes over the tool name.
LEGACY_NAMES_BY_TOOL: Dict[str, tuple] = {}
for _legacy, _current in LEGACY_TOOL_ALIASES.items():
    LEGACY_NAMES_BY_TOOL[_current] = LEGACY_NAMES_BY_TOOL.get(_current, ()) + (_legacy,)

# Pseudo-servers that the consolidated server replaces. Selections that name
# one of these still resolve, so a stale localStorage entry does not silently
# drop a built-in.
LEGACY_SERVER_NAMES = ("canvas", "atlas_agent", "atlas_rag")

CANVAS_TOOL_DESCRIPTION = (
    "Display final rendered content in a visual canvas panel. Use this for: "
    "1) Complete code (not code discussions), 2) Final reports/documents (not "
    "report discussions), 3) Data visualizations, 4) Any polished content that "
    "should be viewed separately from the conversation. Put the actual content "
    "in the canvas, keep discussions in chat."
)

SLEEP_TOOL_DESCRIPTION = (
    "Pause for a number of seconds before continuing. Use this to wait for "
    "long-running external work (a simulation, a submitted job, a remote "
    "process) before checking on it again. Waits longer than the configured "
    "maximum are shortened to that maximum, so call this repeatedly to wait "
    "longer. The wait is aborted if the run is stopped."
)

SEARCH_TOOL_DESCRIPTION = (
    "Search the data sources selected for this conversation and return the "
    "retrieved passages. Pass the search query only -- ATLAS chooses which "
    "sources to read from the user's current selection, falling back to every "
    "source the user can access when nothing is selected."
)

CANVAS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": CANVAS_TOOL_NAME,
        "description": CANVAS_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": (
                        "The content to display in the canvas. Can be markdown, "
                        "code, or plain text."
                    ),
                },
            },
            "required": ["content"],
        },
    },
}

SLEEP_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": SLEEP_TOOL_NAME,
        "description": SLEEP_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "How long to wait, in seconds. Must be greater than 0.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional short note about what is being waited on.",
                },
            },
            "required": ["seconds"],
        },
    },
}

# Single argument by design (#855): identity, authorization and source
# selection are all server-side, so the model has exactly one thing to decide.
SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": SEARCH_TOOL_NAME,
        "description": SEARCH_TOOL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search the selected data sources for.",
                },
            },
            "required": ["query"],
        },
    },
}

ATLAS_TOOL_SCHEMAS = {
    CANVAS_TOOL_NAME: CANVAS_TOOL_SCHEMA,
    SLEEP_TOOL_NAME: SLEEP_TOOL_SCHEMA,
    SEARCH_TOOL_NAME: SEARCH_TOOL_SCHEMA,
}

ATLAS_SERVER_DESCRIPTION = (
    "Built-in ATLAS tools: render final content in the canvas panel, wait "
    "between agent steps, and search the selected data sources. These run "
    "inside ATLAS rather than on an MCP server."
)


def normalize_tool_name(tool_name: Any) -> Any:
    """Map a pre-#855 built-in tool name onto its consolidated name.

    Anything else (including non-strings, which reach this from model-supplied
    tool calls) is returned untouched so callers can keep their own handling.
    """
    if isinstance(tool_name, str):
        return LEGACY_TOOL_ALIASES.get(tool_name, tool_name)
    return tool_name


def normalize_tool_names(tool_names: Optional[List[Any]]) -> List[Any]:
    """``normalize_tool_name`` over a list, preserving order and dropping dupes."""
    if not tool_names:
        return []
    seen = set()
    out: List[Any] = []
    for name in tool_names:
        normalized = normalize_tool_name(name)
        key = normalized if isinstance(normalized, str) else id(normalized)
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def is_atlas_tool(tool_name: Any) -> bool:
    """True for a consolidated built-in tool name (or a legacy alias of one)."""
    return normalize_tool_name(tool_name) in ATLAS_TOOL_SCHEMAS


def atlas_tool_schemas(
    tool_names: List[str],
    *,
    sleep_enabled: bool = True,
    search_enabled: bool = True,
) -> List[Dict[str, Any]]:
    """Schemas for the requested built-ins, minus any that are switched off.

    A disabled tool is omitted rather than advertised-and-refused: agent mode
    reaches the loop without ACL filtering, so a tool in the schema costs a
    step before execution can reject it.
    """
    schemas: List[Dict[str, Any]] = []
    for requested in normalize_tool_names(tool_names):
        if requested == SLEEP_TOOL_NAME and not sleep_enabled:
            continue
        if requested == SEARCH_TOOL_NAME and not search_enabled:
            continue
        schema = ATLAS_TOOL_SCHEMAS.get(requested)
        if schema is not None:
            schemas.append(schema)
    return schemas


def legacy_names_for(tool_name: Any) -> tuple:
    """The pre-#855 name(s) a consolidated built-in used to be called.

    Hook matchers are operator-written regexes over the tool name, so renaming
    the tool would silently stop a hook targeting ``canvas_canvas`` from
    firing -- and that hook may be a deny or approval policy. Callers match
    against these as well as the current name, which over-fires in the safe
    direction rather than quietly disabling a policy.
    """
    if not isinstance(tool_name, str):
        return ()
    return LEGACY_NAMES_BY_TOOL.get(normalize_tool_name(tool_name), ())


def matcher_candidates(tool_name: Any) -> tuple:
    """Every spelling a matcher may reasonably use for ``tool_name``."""
    if not isinstance(tool_name, str):
        return ()
    return (tool_name,) + tuple(n for n in legacy_names_for(tool_name) if n != tool_name)


# Server names the built-in server owns. A configured MCP server using one of
# these would be shadowed by the built-ins at every lookup, so it is rejected at
# config load rather than half-working.
RESERVED_SERVER_NAMES = (ATLAS_SERVER_NAME,) + LEGACY_SERVER_NAMES
