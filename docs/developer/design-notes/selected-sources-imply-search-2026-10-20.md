# Selected Data Sources Imply the Search Tool

Date: 2026-10-20

## What changed

Picking a data source and ticking the built-in `atlas_search` tool used to be
two separate mechanisms, and a user who did only the first got a turn that
read nothing -- plus a warning about it. They are now one mechanism: a
selection of data sources *implies* `atlas_search` (issue #1102).

- The server adds `atlas_search` to the turn's tool list whenever the turn
  carries at least one data source and the deployment offers the tool.
- The tool panels show it as on, say why ("Automatically enabled by selected
  data sources."), and refuse to let it be switched off while the sources are
  selected.
- Deselecting every source removes the implication again.
- The "your data sources were not searched" warning is gone. What is left is
  a one-line warning for the case where the search tool genuinely cannot run.

Availability is not a forced invocation: the model still decides whether a
question is worth a search, and the search shows up in the transcript like any
other tool call.

## Why the tools panel is not the whole authority any more

[Search Only When Selected](search-only-when-selected-2026-09-13.md) made the
tools panel the whole authority on which tools exist, because under #862 a
source selection had put `atlas_search` in the schema "exactly as if the user
had ticked it" -- and a user who had cleared every tool still got
`atlas_search` calls, and a user with another provider's search tool could see
the built-in one invoked over their own pick.

Both of those complaints are about the *schema*, not about the implication
itself, and both are answered by making the implication *visible* rather than
by dropping it:

- The user did not clear every tool and then get a search anyway -- the
  search is on because of the sources they picked, and the panel says so.
- The built-in search does not appear alongside another provider's search
  without the user being able to see that it is there.

So the rule is narrower than #862's: the tool is implied only by the *data
source selection*, it is always shown as implied in the panel, and it is never
implied for a turn that carries no sources.

## What was deliberately not added

- **No opt-out, and nothing new persisted.** The implication is a property of
  the turn, not a setting. An opt-out would have produced the state #921 was
  built to avoid: sources selected, tool not available, and the user with no
  way to tell why.
- **No plain RAG fallback.** A turn that carries sources and cannot search
  them says so; it does not quietly route to a different retrieval path.
- **No forced invocation.** The tool is offered, not called.

## Where the resolution happens

`atlas/application/chat/utilities/effective_tools.py` holds the rule:

```python
resolve_effective_tools(selected_tools, selected_data_sources, config_manager)
```

The orchestrator resolves the turn's tool list *before* routing, so the
routing itself sees the effective list:

- sources selected + the RAG tools flags on -> the turn is a tools (or agent)
  turn with `atlas_search` in its schema, even if the user ticked nothing;
- sources selected + the flags off -> the tool is not offered, and the turn
  falls through to the RAG-mode route, which reads the sources itself;
- no sources -> the user's selection is used exactly as given.

The flags that gate the tool (`FEATURE_RAG_ENABLED` and
`FEATURE_ATLAS_RAG_TOOLS_ENABLED`) are read in one place --
``atlas_server.search_tools_enabled``, next to ``launch_tool_enabled`` -- and
the schema builder, the tool ACL, the execution gate, the config payload and
this resolution all call it, so they cannot drift apart.

The mode runners build their schema from exactly the list they are handed;
they do not resolve anything themselves. The orchestrator is the single
entry point, and its tests pin what each mode receives.

## Two cases where the implication does not apply

The implication adds a tool the user did not tick, so two things get the last
word over it:

- **A model that cannot call tools.** The tools are stripped for such a
  model, so the turn has none; adding the search tool back would hand it a
  schema it cannot use and put it in the tools branch instead of the route
  it always took. The implication is skipped, and the turn keeps its
  sources.
- **A UserPromptSubmit hook that narrows the tool list.** A hook is a
  policy: it may narrow the tools (and the sources) a turn may use. The
  effective tool list is resolved *before* the hook runs, so the hook sees
  the tools the turn would really offer -- the implied search tool included
  -- and can strip it. The hook's list is intersected with that list, so it
  can only narrow, never grant.

A hook that does not touch the tool list changes nothing, and a hook that
drops an unrelated tool leaves the implied one alone.

## What still enforces the boundary

Implying the tool does not widen what a search may read:

- the data source selection stays the *ceiling* on what `atlas_search` may
  reach -- the tool executor intersects the requested sources with the
  selection and with the user's own authorized set;
- the built-in `atlas` tools run in-process and are exempt from the
  turn-level tool check, exactly as before; the sources they read are not
  (a selected corpus that discovery does not offer at the active level is
  refused, and the query path checks again at query time);
- authorization still runs on the effective tool list, in the tools branch,
  before the schema is built.

## The warning that replaced the old one

The old warning -- *"Your data sources were not searched"* -- fired whenever
a tools turn carried sources and the search tool was not in the tool list.
That was the ordinary case once the implication was dropped, and it was
misleading even then: the model not calling the tool is not a failure.

What is left is the case where the sources really cannot be read: the
deployment has the search tool switched off, or authorization took it away.
The warning says exactly that, in one line, and names the flag when the flag
is the reason.

A search that *fails* is a different thing and is already reported as what it
is: the tool call comes back failed, with the backend's reason, in the
transcript. That is the accurate report of an outage -- the old warning was
not, because it also fired on every turn where the model simply did not
deem a search worthwhile.

Sources the client expanded on its own (the RAG toggle on, nothing
hand-picked) stay silent: the user did not choose them.

## Frontend

- `frontend/src/utils/searchToolAvailability.js` holds the shared rule. The
  catalogue is the authority on whether the deployment offers the tool at all
  -- the backend leaves it out when the feature is off -- so no extra feature
  flag had to be kept in step.
- The panels read the *effective* data sources -- the ones the turn would
  really carry, after the active compliance level has had its say -- and the
  selected model's tool support, so the panel never claims a tool the server
  is not going to offer.
- Both the chat-bar **Tools** menu and the Tools and Settings panel show the
  automatic state, with the note and a non-toggleable control. A tool that is
  *also* selected in its own right says so too: the sources imply it, but
  the user's own tick is what keeps it on once the sources are deselected.
- The composer's "agent mode has nothing to act on" callout counts the
  implied tool, so a turn with sources selected is not told it has no tool.

## What a reviewer should check

That the model is offered the search tool exactly when the turn carries
sources and the feature is on -- and that it is offered nothing more than
that. `atlas/tests/test_effective_tools.py` pins the resolution, the warning
and the routing; `frontend/src/test/search-tool-availability.test.js` and
`frontend/src/test/search-tool-auto-enable.test.jsx` pin the panel state and
the source transitions.

The screenshots the review asked for live in
`test/pr-validation/assets/pr1102-screenshots/`: the locked search-tool
state in both panels, the same two panels with every source deselected, and
a real sources-only turn in which the model called `atlas_search` and the
answer cited the passages it read.

## Compatibility

- Legacy tool names still normalize (`atlas_rag_query` -> `atlas_search`), and
  a selection that already names the search tool is never duplicated by the
  implication.
- Saved conversations replay unchanged: they hold tool-call rows, and rows
  are rendered, not re-executed.
- An explicit selection of `atlas_search` survives a deselection of every
  source: the implication only ever adds.
