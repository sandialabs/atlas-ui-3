# Citations for Tool-Based Search

Date: 2026-08-31

## What was broken

Selecting data sources and asking a question produced an answer with no sources
attached. Nothing in the UI said what had been read.

The cause was left behind by #862, which made `atlas_search` an explicit tool
call. Before that change, a turn carrying `data_sources` went through
`LiteLLMCaller.call_with_rag_and_tools`, which did **three** things. The design
note for #862 accounts for one of them:

1. queried the sources and injected a `Retrieved context from ...` system
   message -- the silent retrieval that #862 set out to remove;
2. appended `_build_citation_instructions(metadata)` to the system prompt --
   the text telling the model to write `[1]`, `[2]`;
3. appended `_format_rag_references(metadata)` -- a literal `**References**`
   markdown block -- to the assistant's own message.

#862 deleted both methods, and (2) and (3) went with (1). Neither is mentioned
in that PR; the citation apparatus was collateral.

What made it invisible rather than obviously broken is that the citation UI was
never a data path. `Message.jsx` triggered on
`latexRestoredHtml.includes('References')`, and `ragCitations.js` recovered the
source list by running regexes over the rendered HTML looking for
`<strong>References</strong>`. With nothing writing that block, the extractor
found nothing and there was no error to notice. A second, independent blocker
sat underneath: `VITE_FEATURE_RAG_CITATIONS` defaulted to `false` everywhere,
so even RAG mode rendered no chips.

So this was a **design gap**, not a regression that could be rewired: tool-based
retrieval had nowhere to put citations. The data existed --
`mcp_execution._tool_references()` had been building a `references` list since
#791 -- and reached the browser, where `processToolResult` had no branch for it
and it was pretty-printed as JSON inside a collapsed tool row.

## Why scraping the answer could not be repaired

Scraping worked only because exactly one retrieval happened per turn, before the
model was asked anything, so there was a single response to append a block to.
A tool has none of those properties. The model may search zero times, once, or
six; the answer is written across those calls; and two calls can return the same
document. There is no "the RAG response" to append to any more.

It was also the reason `_sanitize_snippet` existed: passage text sat in the same
string the extractor parsed, so a snippet that started with `1. ` could
masquerade as a reference entry. Structured data removes that surface entirely.

## The design

Citations are a **turn-scoped register**, carried on the tool execution context
exactly like the sleep tool's `TURN_BUDGET_KEY` -- mutable, one per turn,
forwarded by `tool_executor` to every call and read back by the turn's owner.

`atlas_search` registers every document it returns.
`domain/chat/citation_register.py` assigns each one a number that is:

- **Identity-keyed**, so a document returned by two searches in one turn keeps
  one number. Identity is `url`, then `filename`, then `document_ref` -- in that
  order, and the order matters. `document_ref` sounds authoritative but indexes
  *one response's* result list, so the same document comes back as 3 from one
  search and 5 from the next. Trusting it first is what made a repeated hit take
  two numbers; it was caught against the reference mock, where two searches each
  returned `Code of Conduct, pol-003.txt` and it was numbered twice.
- **Scoped by data source**, because filename is in that chain and two corpora
  may each hold a `policy.pdf` that are not the same document.
- **Stable across turns.** The register is seeded from the citations persisted
  on earlier assistant messages, carrying forward both the count (a new document
  never reuses a number) and the identities (a document cited again keeps the
  number it had). `[3]` names one document for the whole transcript; restarting
  per turn makes a scrollback actively misleading, since the same marker
  resolves differently further up.

Numbering stops at `MAX_CITATION_NUMBER = 99`, and that bound is set by the
renderer rather than by taste: `processCitationBadges` matches `[\d{1,2}]`, so a
`[100]` in an answer would stay plain text instead of becoming a chip. Widening
that regex would make it match three-digit array indices in prose, so the ceiling
gives way instead. The check is conversation-wide -- it tests the next number to
issue, not the current turn's entry count, which a fresh register always starts
at zero.

The numbers are echoed into the tool result, because the model can only cite a
number it has been shown, and `SEARCH_TOOL_DESCRIPTION` now carries the
instruction to use them. That instruction lives on the tool rather than in the
system prompt deliberately: it travels with the schema wherever the tool is
offered -- agent mode included -- and costs nothing on a turn that is not
offered it.

At turn close the register is both **persisted** (message metadata, so a
reloaded conversation renders its sources without re-running retrieval, and the
next turn can continue the numbering) and **published** (a `citations` event the
frontend attaches to the assistant message). One event per turn, not one per
search: a turn may search three times and cite two documents, and a per-call
event would leave the UI showing sources for a call whose results the model
discarded.

`Message.jsx` now triggers on `message.citations` and renders
`renderReferencesSection()` from the structured list.

## Multi-turn, and the digest

`agent_digest.py` truncates every tool result to `_MAX_RESULT_CHARS = 400`.
That is right for passage text and wrong for citation numbers: a follow-up turn
that cannot see which document was `[3]` either renumbers it or stops citing.
`get_messages_for_llm` therefore folds a separate, identity-only line naming the
conversation's numbers (capped at `MAX_FOLDED_CITATIONS = 40`), no snippet text.
A conversation that never cited anything adds nothing to the prompt.

## RAG mode still scrapes, and that is correct

RAG mode -- sources selected, no tools -- keeps `call_with_rag` /
`stream_with_rag`, which still pre-inject and still append `**References**`.
It has no tools schema and no model turn to spend, so pre-injection is the whole
design there rather than a hidden step, exactly as #862 left it. `Message.jsx`
keeps both paths and prefers the structured one when both are somehow present:
it is the one that knows the numbers. `extractSourceLabels` and
`processReferencesSection` are therefore kept, not deleted.

## The feature flag

`VITE_FEATURE_RAG_CITATIONS` now defaults to `true` in `.env.example`, both
Dockerfiles, `quay-publish.yml`, and the two start scripts. It is a **build-time**
Vite constant, so a build that does not export it dead-code-eliminates the whole
citation branch -- which is why `agent_start.sh` exports it rather than relying
on the root `.env` (Vite reads `frontend/`, not the project root).

## What a reviewer should check

That the numbering is stable in all three directions, which is the whole point:
within one search, across searches in a turn, and across turns.
`test_citation_register.py` pins each, and
`test_atlas_rag_agent_tools.py::test_two_searches_in_one_turn_share_one_numbering`
pins the multi-call case through the real execution path.

Verified end to end against the reference RAG mock with a live model: six
`atlas_search` calls over two turns produced `[1]`-`[5]`, then a second turn that
re-cited `[5]` under its original number and continued at `[6]`, `[7]`.
