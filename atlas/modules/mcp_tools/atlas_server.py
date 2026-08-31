"""The consolidated ``atlas`` built-in tool server (issue #855).

ATLAS grew three built-in, non-MCP "pseudo-servers" one at a time: ``canvas``
(render final content in the side panel), ``atlas_agent`` (wait between agent
steps) and ``atlas_rag`` (query retrieval sources). Each showed up in the tools
panel as its own server with one or two tools, which made a short list of
built-ins look like a crowd of servers.

They are now a single server, ``atlas``, exposing ``atlas_canvas``,
``atlas_sleep``, ``atlas_search`` and ``atlas_discover_sources``. The old fully-qualified names are still
accepted -- persisted tool selections, saved conversations and non-UI clients
all carry them -- and are normalized to the new names at the edges via
``normalize_tool_name``.

``atlas_search`` requires exactly one argument, ``query``. The data sources it
reads are the ones selected in the UI (falling back to everything the user is
authorized for when nothing is selected), so the model never picks its own
sources. Two *optional* knobs tune retrieval rather than reach:
``max_results`` and ``depth``. Both only affect how much is retrieved and how
hard the backend works for it -- neither can widen the set of sources, so they
stay outside the authorization boundary. They are honoured by v2 RAG sources
(mapped onto the ``search_kwargs`` block of the ATLAS RAG v2 contract) and
ignored by v1 sources, whose request body has no equivalent fields.

``atlas_discover_sources`` lists the sources the user can actually reach, so a
model can say which corpus an answer came from -- or tell the user that the
thing they asked about is not in any source they have access to.
"""

from typing import Any, Dict, List, Optional

ATLAS_SERVER_NAME = "atlas"

CANVAS_TOOL_NAME = "atlas_canvas"
SLEEP_TOOL_NAME = "atlas_sleep"
SEARCH_TOOL_NAME = "atlas_search"
DISCOVER_TOOL_NAME = "atlas_discover_sources"

ATLAS_TOOL_NAMES = (
    CANVAS_TOOL_NAME,
    SLEEP_TOOL_NAME,
    SEARCH_TOOL_NAME,
    DISCOVER_TOOL_NAME,
)

# Pre-#855 fully-qualified names -> consolidated names.
LEGACY_TOOL_ALIASES = {
    "canvas_canvas": CANVAS_TOOL_NAME,
    "atlas_agent_sleep": SLEEP_TOOL_NAME,
    "atlas_rag_query": SEARCH_TOOL_NAME,
    "atlas_rag_discover_data_sources": DISCOVER_TOOL_NAME,
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
    "retrieved passages. ATLAS chooses which sources to read from the user's "
    "current selection, falling back to every source the user can access when "
    "nothing is selected -- so pass the query, and optionally tune how much is "
    "retrieved with 'max_results' and how hard to look with 'depth'. "
    # The instruction lives on the tool, not in the system prompt, because that
    # is what survives agent mode: it travels with the schema whenever the tool
    # is offered, and costs nothing on a turn that is not offered it (#874).
    "Each result is numbered. Cite them inline as [1], [2] immediately after "
    "the claim each supports, and never cite a number you were not given -- "
    "the numbers are stable for the whole conversation, so a source keeps its "
    "number across repeated searches and later turns."
)

DISCOVER_TOOL_DESCRIPTION = (
    "List the data sources this user can search, as server-qualified IDs "
    "(server:source_id). Use it to report which corpus an answer came from, or "
    "to tell the user that what they are asking about is not in any source "
    "they can reach. Takes no arguments; searching does not require calling "
    "this first."
)

# Retrieval effort. These are deliberately words, not numbers: the model is
# asked how hard to look, and ATLAS decides what that costs on the backend.
SEARCH_DEPTHS = ("quick", "standard", "deep")
DEFAULT_SEARCH_DEPTH = "standard"

# Upper bound on ``max_results``. A model asking for 500 passages would blow
# past the context window long before the backend refused, so the request is
# clamped rather than rejected.
MAX_SEARCH_RESULTS = 50

# ``depth`` -> extra v2 ``search_kwargs``. ``standard`` adds nothing, leaving
# every knob at the backend's own default.
_DEPTH_SEARCH_KWARGS = {
    "quick": {
        "rerank": False,
        "top_k_vector": 3,
        "top_k_full_text": 3,
    },
    "standard": {},
    "deep": {
        "rerank": True,
        "top_k_vector": 20,
        "top_k_full_text": 20,
        "expanded_window": [500, 500],
    },
}

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

# One *required* argument by design (#855): identity, authorization and source
# selection are all server-side, so the only thing the model must decide is
# what to look for. ``max_results`` and ``depth`` are optional retrieval knobs
# -- they change how much comes back, never which sources are reachable.
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
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_RESULTS,
                    "description": (
                        "Optional. How many passages to return per source. "
                        f"Clamped to {MAX_SEARCH_RESULTS}. Omit to use the "
                        "source's configured default."
                    ),
                },
                "depth": {
                    "type": "string",
                    "enum": list(SEARCH_DEPTHS),
                    "description": (
                        "Optional search effort. 'quick' skips reranking for a "
                        "fast look-up, 'standard' (default) uses the source's "
                        "configured behaviour, and 'deep' widens the candidate "
                        "pool and returns more surrounding context for hard or "
                        "open-ended questions."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

DISCOVER_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": DISCOVER_TOOL_NAME,
        "description": DISCOVER_TOOL_DESCRIPTION,
        # No arguments: the authenticated user is supplied by ATLAS and is the
        # only input that decides what comes back. Nothing here is model input,
        # so nothing here can widen the result.
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

ATLAS_TOOL_SCHEMAS = {
    CANVAS_TOOL_NAME: CANVAS_TOOL_SCHEMA,
    SLEEP_TOOL_NAME: SLEEP_TOOL_SCHEMA,
    SEARCH_TOOL_NAME: SEARCH_TOOL_SCHEMA,
    DISCOVER_TOOL_NAME: DISCOVER_TOOL_SCHEMA,
}

ATLAS_SERVER_DESCRIPTION = (
    "Built-in ATLAS tools: render final content in the canvas panel, wait "
    "between agent steps, search the selected data sources and list which "
    "sources are available. These run "
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
        if requested in (SEARCH_TOOL_NAME, DISCOVER_TOOL_NAME) and not search_enabled:
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


def search_kwargs_for(
    depth: Any = None,
    max_results: Any = None,
) -> Optional[Dict[str, Any]]:
    """Translate the model-facing search knobs into v2 ``search_kwargs``.

    Returns ``None`` when neither knob was usably supplied, which leaves the
    source's own configuration in charge. Both arguments are model-supplied, so
    anything unrecognized is dropped rather than forwarded: a bad ``depth``
    falls back to the default, and ``max_results`` is coerced and clamped. Note
    that only v2 sources have a ``search_kwargs`` block -- v1 ignores both.
    """
    kwargs: Dict[str, Any] = {}

    depth_key = depth if depth in SEARCH_DEPTHS else DEFAULT_SEARCH_DEPTH
    kwargs.update(_DEPTH_SEARCH_KWARGS.get(depth_key, {}))

    top_k = None
    if isinstance(max_results, bool):
        # ``bool`` is an ``int`` subclass; ``max_results=True`` is a mistake,
        # not a request for one passage.
        top_k = None
    elif isinstance(max_results, int):
        top_k = max_results
    elif isinstance(max_results, str):
        try:
            top_k = int(max_results.strip())
        except (TypeError, ValueError):
            top_k = None
    if top_k is not None:
        kwargs["top_k_final"] = max(1, min(MAX_SEARCH_RESULTS, top_k))

    return kwargs or None


# Server names the built-in server owns. A configured MCP server using one of
# these would be shadowed by the built-ins at every lookup, so it is rejected at
# config load rather than half-working.
RESERVED_SERVER_NAMES = (ATLAS_SERVER_NAME,) + LEGACY_SERVER_NAMES
