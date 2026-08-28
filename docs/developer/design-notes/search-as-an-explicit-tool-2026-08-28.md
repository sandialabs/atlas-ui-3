# Search Is an Explicit Tool Call

Date: 2026-08-28

## What changed

`atlas_search` existed as a tool before this change, but almost nothing ever
called it: retrieval happened on its own, before the model was asked anything.

Any turn that carried `data_sources` was routed through
`LiteLLMCaller.call_with_rag_and_tools` (or the streaming
`stream_with_rag_and_tools`). Those methods queried every selected source in
parallel, built a `Retrieved context from ...` system message, inserted it
ahead of the user turn, and only then called the LLM with the tools schema.

The consequences were all the same shape -- the search was invisible and
nobody chose it:

- **The user could not see it happen.** No tool call was published, so the
  chat showed an answer with citations and no account of where they came from.
- **The model could not choose the query.** The query was whatever
  `_query_all_rag_sources` derived from the conversation, not something the
  model decided to look for.
- **The model could not choose *whether* to search.** Every turn paid for a
  retrieval round trip, including "thanks, that's all" ones.
- **One search per turn, always first.** The model could not read a result and
  search again with a better query, which is the thing that makes a search tool
  useful.

Retrieval is now an ordinary tool call, the way Tavily or a Perplexity MCP
server works: the model calls `atlas_search({query, max_results?, depth?})`,
the call renders in the chat like any other tool, and the passages come back
as its tool result for the model to reason over.

## How

- `agentic_loop._call_llm` / `_call_llm_streaming` and both tools-mode call
  sites now always take the plain `call_with_tools` / `stream_with_tools`
  path. The `data_sources` branch is gone.
- `LiteLLMCaller.call_with_rag_and_tools` and
  `LiteLLMStreaming.stream_with_rag_and_tools` are **deleted**, along with
  their `LLMProtocol` declarations and the RAG branch in
  `error_handler.safe_call_llm_with_tools`. Leaving them in place would have
  left a second, silent retrieval path one call site away from coming back.
- Nothing changed in the execution path: `atlas_search` already ran through
  `tool_executor` -> `ToolManager.execute_tool` ->
  `_execute_atlas_rag_tool`, with its own feature gate and its own
  authorization intersection against the user's discovered sources.

RAG mode (`call_with_rag` / `stream_with_rag`, no tools) is untouched. It has
no tools schema and no model turn to spend, so pre-injection is the whole
design there rather than a hidden step.

## The data-source selection still means something

Two things, in fact, and it is worth keeping them apart:

1. **A ceiling.** `mcp_execution` intersects the requested sources with the
   selection and then with the user's authorized set. A model cannot search a
   source the user did not pick. That was already true and is unchanged.
2. **Availability.** A selection of data sources now also *offers*
   `atlas_search`, via `application/chat/utilities/search_tool_selection.py`,
   exactly as if the user had ticked the tool.

(2) is a deliberate compromise. A literal reading of "search only happens when
the model calls the tool" would leave a user who picks a source but never
ticks the tool with a selection that does nothing at all and says nothing
about it -- trading one silent behaviour for another. Offering the tool keeps
the decision with the model while keeping the source picker meaningful. It is
gated on `FEATURE_RAG_ENABLED` and `FEATURE_ATLAS_RAG_TOOLS_ENABLED`, and it
never duplicates a tool the user already selected under either its current or
its pre-#855 name.

## Compatibility

- Legacy tool names still normalize (`atlas_rag_query` -> `atlas_search`,
  `atlas_rag_discover_data_sources` -> `atlas_discover_sources`), in
  selections, saved conversations and hook matchers alike.
- Saved conversations replay unchanged: they hold tool-call rows, and rows are
  rendered, not re-executed.
- `atlas_discover_sources` was always an explicit call and is unaffected.

## What a reviewer should check

That no code path reaches the LLM with retrieval already folded in. The tests
that pinned the old behaviour were removed with the methods they covered; the
ones that replace them assert the *absence* of injection -- see
`test_agentic_loop.py::TestAgenticLoopSearchIsATool` and
`test_tools_mode_iteration.py::test_data_sources_do_not_inject_context_and_offer_the_search_tool`,
which both assert the model saw exactly the messages it was given.
