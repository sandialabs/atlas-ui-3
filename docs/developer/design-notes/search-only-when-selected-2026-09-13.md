# Search Is Only the Tool the User Ticked

Date: 2026-09-13

## What changed

Selecting a data source no longer offers `atlas_search`. The tool is in the
LLM's schema only when the user actually ticked it in the tools panel, and a
turn that carries sources but nothing to read them is told so instead of
quietly searching anyway (issue #921).

Under #862 the selection also *implied* the tool: "exactly as if the user had
ticked it". That compromise traded one silence for another, and the trade
turned out to be the wrong side of the bargain:

- A user who cleared every tool still got `atlas_search` calls, because the
  sources rode along and the implication put the tool back in the schema.
- Worse, a user who had selected a *different* provider's search tool got the
  built-in tool called over their own -- a prompt like "use search find the
  paid leave policy" resolves to whichever tool named `search`, and the
  implied `atlas_search` was sitting right there.

The tools panel is now the whole authority on which tools exist. Expected
behavior, straight from the issue: *if atlas-search is not enabled then it
should not be used.*

## What keeps the source picker meaningful

Nothing in the picker is stranded, because the routing that already existed
covers both halves of "sources without the search tool":

- **No tools selected** routes to RAG mode, which reads the sources itself
  (pre-injection, citations attached) -- the same behavior a source-only turn
  had before #862, reached again because nothing intercepts it.
- **Agent mode with no tools** is downgraded to that same RAG turn, with the
  existing "agent mode needs at least one tool" note.
- **`only_rag` turns** bypass tools mode and likewise read their sources.

The one genuinely stranded case is *sources plus other tools, search tool not
ticked*: tools/agent mode runs the ticked tools and nothing reads the
sources. `ChatOrchestrator._check_data_sources_reachable` (formerly
`_resolve_search_tool`, which no longer resolves anything) publishes a
warning naming the way out -- select `atlas_search`, or deselect the tools to
fall through to plain RAG. The warning is about the *turn*, not the feature
flags: since #921 the ordinary reason the search tool is missing is that the
user did not tick it, and a flag being off (`FEATURE_ATLAS_RAG_TOOLS_ENABLED`)
strands the sources the same way. A turn that names the tool under either its
current or its pre-#855 name (`atlas_rag_query`, what replayed conversations
carry) is never warned about.

## Frontend

- The chat-bar guard that blocked agent-mode sends with no tools selected is
  gone. The composer shows a small persistent warning banner instead, and the
  send goes through -- the backend downgrades the turn and says so in the
  transcript. Blocking the send took away the user's typed message to make a
  point the banner can make without it.
- The tools tab's footer button is "Save and Close": it commits the staged
  selections and dismisses the whole combined panel. (Unrelated to the search
  fix, shipped in the same PR; see `ToolsPanel.jsx`.)

## What a reviewer should check

That no turn reaches the LLM with a tool the user did not select. The tests
that pinned the implication were inverted:
`test_agentic_loop.py::TestAgenticLoopSearchIsATool` and
`test_tools_mode_iteration.py` now assert the model saw exactly the user's
selection, and `test_data_sources_reachability.py` pins the warning (and its
silences: no tools, `only_rag`, legacy tool name, no config).

## Compatibility

- Saved conversations replay unchanged: they hold tool-call rows, and rows
  are rendered, not re-executed.
- Legacy tool names still normalize everywhere; selecting `atlas_rag_query`
  still counts as having the search tool, both for the schema and for the
  reachability warning.
- The source selection still scopes what `atlas_search` may read
  (`mcp_execution` intersects requested with authorized), and an
  `atlas_search` selection with no sources still means "everything I can
  reach" (#855).