# Compliance and Data Security

Last updated: 2026-10-08

The compliance system is designed to prevent the unintentional mixing of data from different security environments. This is essential for organizations that handle sensitive information.

## Compliance Levels and Data Classifications

The levels themselves -- the data classifications a conversation can run at, such as `UUR`, `ITAR` or `ECI` -- are defined in `atlas/config/compliance-levels.json` (package default) and can be customized in `config/compliance-levels.json` (or `compliance-levels.json` in whatever directory `APP_CONFIG_DIR` names). A file in the user config directory replaces the package default entirely, so a custom file must list every level you use.

Every component that can receive conversation data -- LLM models (including LiteLLM gateways and their allowlisted models), MCP servers and RAG sources -- declares which classifications it is **explicitly approved to receive** with `allowed_data_classifications`:

```yaml
# llmconfig.yml
models:
  model-x:
    # ...
    allowed_data_classifications: [UUR, ITAR, ECI]
  model-y:
    # ...
    allowed_data_classifications: [UUR]
```

```json
// mcp.json (rag-sources.json takes the same field per source)
{
  "google-search":   { "url": "...", "allowed_data_classifications": ["UUR"] },
  "internal-search": { "url": "...", "allowed_data_classifications": ["UUR", "ITAR", "ECI"] }
}
```

Names are resolved through the level aliases at load. A name that matches no defined level is dropped with a warning, so a typo can only narrow what a component is approved for, never widen it.

> **Upgrade note:** define every classification you use in `config/compliance-levels.json` (for example, `CUI` is not in the bundled defaults). A component whose only classification is undefined -- in `allowed_data_classifications` or a legacy `compliance_level` -- ends up declaring nothing, so it is unavailable in **every** classified session.

## The Rule

The active conversation classification -- the level selected in the header -- is the authority. A component may be used only when that classification is a member of its `allowed_data_classifications`:

| Active classification | Model X (`UUR, ITAR, ECI`) | Model Y (`UUR`) | google-search (`UUR`) | internal-search (`UUR, ITAR, ECI`) |
|---|---|---|---|---|
| UUR  | yes | yes | yes | yes |
| ITAR | yes | no  | no  | yes |
| ECI  | yes | no  | no  | yes |

- **Deny by default.** A component that declares no classifications (neither `allowed_data_classifications` nor a legacy `compliance_level`), or an empty list, is approved for no classified session. This matters most for MCP servers, which can be data egress points.
- **No implication between levels.** `allowed_with` in `compliance-levels.json` no longer grants access across levels: an ITAR session never reaches a UUR-only model or tool because ITAR "allows" UUR. List every classification a component may receive on the component itself.
- With no level selected ("All Levels", only offered when a level is not required), nothing is filtered in the UI and no turn is refused for its components. RAG queries keep a floor: a source that declares classifications must share at least one with the selected model. Nothing else is checked: in particular, RAG results in a no-level turn can reach any tool server the user selected. If that matters for your deployment, turn on `FEATURE_COMPLIANCE_LEVEL_REQUIRED`. Use `FEATURE_COMPLIANCE_LEVEL_REQUIRED` (below) to remove the no-level choice.
- A level the deployment does not define is refused, rather than treated as no level.

## Server-Side Enforcement

The rule is enforced on the server, not only in the UI, so a stale bundle, the CLI, the Python client or a hand-crafted WebSocket client cannot bypass it. Before a chat turn runs, the server checks the selected model, the MCP server behind every selected tool, and every selected data source against the active classification. If any is not approved, the turn is refused with a message naming them, before the session is touched or anything is called. If the check itself cannot run (a broken configuration lookup), a classified turn is refused rather than run unchecked.

Tool calls are checked again when they execute. A call the model makes to a tool on a server not approved for the active classification is refused, even if that tool was never selected (a hallucinated or prompt-injected call). The same applies to a server that declares nothing.

A RAG backend's discovery can also return `allowed_data_classifications` per corpus. A corpus can only narrow its server's list (entries the server does not list are ignored); one that sends no list inherits its server's. A per-corpus `compliance_level` from discovery is shown as a badge but is not a boundary by default, because existing backends send one (often `CUI`) for every corpus; a server can opt in to reading it as the corpus's one-element list with `legacy_corpus_classifications: true` (see [Legacy Per-Corpus Classifications](external-rag-api.md#legacy-per-corpus-classifications)). For a classified turn with selected data sources, the server runs discovery at the active level for just those servers and refuses any selected corpus it does not offer. A corpus whose backend does not answer, or that the user cannot see, is refused as unconfirmed rather than queried. RAG queries are checked again at query time against the active classification, server and each requested HTTP corpus alike (a batch is refused whole if any corpus fails, and a corpus the backend's discovery does not list is refused), which also covers `atlas_search` calls the model makes during a turn; discovery for the `atlas_search` tool only offers sources approved for it. The built-in `atlas` tools (canvas, sleep, search, discover sources) run in-process and are exempt from the tool check; search reaches only sources that pass their own checks.

## Saved Conversations Keep Their Classification

A saved conversation can only be continued under the classification it was created under (issue #1042). Without this rule, history saved at one level (say `ITAR`) could be reopened later at another (`UUR`) and sent to a model or tool approved only for the second. The component check above looks at the components, not at where the history came from.

**The record.** When the first turn of a conversation runs, the server stores the turn's validated active level in the conversation's metadata as `data_classification`. A conversation started with no level selected ("All Levels") records `null` (unclassified). While compliance levels are disabled nothing is recorded: those conversations are stored like legacy ones, so they can be stamped if you enable levels later. The value comes from the server's own validation, never from a client field, and it never changes: the repository refuses a save that would change it, drop it, or add one to a conversation that has none. `GET /api/conversations`, `/search` and `/{id}` return it as `data_classification` with `data_classification_state` (`classified`, `unclassified`, `legacy` or `invalid`).

**The rule.** The active level of a turn must equal the conversation's recorded level, after alias resolution. Levels have no order, so there is no "more sensitive". A conversation is never moved to another level, silently or otherwise. To continue a conversation, select its level. To work at another level, start a new conversation.

| Recorded | Active level | Compliance levels enabled | Result |
|---|---|---|---|
| `ITAR` | `ITAR` | yes | continues |
| `ITAR` | `UUR` or none | yes | refused |
| `ITAR` | (none) | no | refused |
| unclassified | none | yes | continues |
| unclassified | any level | yes | refused |
| legacy (no record) | anything | yes | refused |
| legacy (no record) | (none) | no | continues |
| a level no longer defined, or an unreadable record | anything | either | refused |

**Where it is enforced.** On the server, on every path that loads a conversation's history:

- **Every chat turn.** The history the session holds is bound to its conversation's record. This covers resuming from the sidebar, rehydration after a WebSocket reconnect, parallel conversation runs and sub-conversations, and switching the level, model or tools partway through a conversation. Omitting the conversation id does not detach the history. A refused turn sends nothing to a model or tool and is not saved.
- **Restore** (`restore_conversation`). When the frame carries the client's level (`compliance_level_filter`), a mismatch is refused before the session is touched. Either way the session is bound to the stored record, so an older client that leaves the level out is still stopped at its next turn.
- **REST fetch.** `GET /api/conversations/{id}?compliance_level=<level>` (an empty value means no level) answers a mismatch with `409` and the classification only, never the messages. The UI always sends it. Without the parameter the owner can still read the conversation, as with the export: viewing your own history does not send it anywhere.
- **Steering.** A message sent into a running agent loop is checked against that conversation's record with the same rule, and refused on a mismatch.
- **Store read failures.** If the stored record cannot be read when a turn needs it, the turn is refused (nothing is sent) and the next turn retries the load.

Refusals are logged at WARNING with the conversation id, user, recorded and active levels, and never the conversation content.

**Legacy conversations.** Conversations saved before this release, or while compliance levels were disabled, have no record. While compliance levels are enabled their provenance is unknown, so they are not assumed to be any level: they stay listed (marked "Unrecorded level") and exportable, but cannot be opened into the chat or continued. With compliance levels disabled they keep working as before and stay unrecorded. If you know what a set of legacy conversations holds, record it explicitly:

```bash
# Stop the app first when using DuckDB (exclusive file lock).
python scripts/stamp_conversation_classification.py --level UUR --user alice@example.com --dry-run
python scripts/stamp_conversation_classification.py --level UUR --user alice@example.com
python scripts/stamp_conversation_classification.py --unclassified --id <conversation-id>
```

The script only touches conversations with no record and never rewrites one.

**Browser-local history** (save mode "local"). The browser records the conversation's level in its local copy, and the UI applies the same rule. The server has no stored record for client-held history, so it binds a restored local conversation to the level the client reopens it at, or treats it as legacy when the client sends none. This is no stronger than the user pasting the same text into a new conversation.

## Migrating from `compliance_level`

`compliance_level` is deprecated but still read:

1. A component with only `compliance_level: X` is treated as `allowed_data_classifications: [X]`.
2. When both are set, `allowed_data_classifications` wins and a deprecation warning is logged at load. Remove `compliance_level` once you have migrated.
3. For a LiteLLM gateway, the most specific declaration wins: the allowlisted model's `allowed_data_classifications`, then its `compliance_level`, then the gateway's `allowed_data_classifications`, then the gateway's `compliance_level`.
4. HTTP RAG discovery may return `allowed_data_classifications` per source alongside `compliance_level`; MCP RAG resources may return `allowedDataClassifications`. A resource that declares nothing inherits its server's classifications. For an HTTP backend that only sends a per-corpus `compliance_level`, set `legacy_corpus_classifications: true` on the server until the backend sends lists.

> **Behavior change:** before this release, a level's `allowed_with` list let a session use components at other levels (the bundled HIPAA level allowed SOC2 components), and a component with no level was usable from any session on the server side. Both now fail closed. To keep a component usable at several levels, list them all in its `allowed_data_classifications`. `allowed_with` is still accepted in `compliance-levels.json` (and returned by `/api/compliance-levels`) for compatibility, but it no longer affects access; a WARNING at startup names any level whose `allowed_with` lists other levels.

## Enabling the Compliance Selector

Set `FEATURE_COMPLIANCE_LEVELS_ENABLED=true`. The header then shows a compliance level selector (in the overflow menu on narrow screens) listing every defined level, plus "All Levels" for no filter.

## Requiring a Compliance Level

By default a user can pick "All Levels", which applies no filter. To require every chat turn to run under a concrete level, also set:

```bash
FEATURE_COMPLIANCE_LEVELS_ENABLED=true
FEATURE_COMPLIANCE_LEVEL_REQUIRED=true
# Optional: the level a session starts on. Must name a level (or alias) in
# compliance-levels.json; when unset or undefined, the first defined level is used.
COMPLIANCE_DEFAULT_LEVEL=Internal
```

`FEATURE_COMPLIANCE_LEVEL_REQUIRED` has no effect unless `FEATURE_COMPLIANCE_LEVELS_ENABLED` is also on. With both set:

- **UI:** the selector has no "All Levels" option. A user with no saved level, or with a saved level the deployment no longer defines, starts on `COMPLIANCE_DEFAULT_LEVEL` (or the first defined level). Sending is refused, with a message, while no level is set -- for example if the level definitions failed to load.
- **Server:** every chat turn must carry a level that names a defined level or alias. A turn with no level, or with an undefined one, is rejected with a validation error before any model, tool or data source is called. This covers the browser, stale or hand-crafted WebSocket clients, background runs, the Python `AtlasClient` (`compliance_level=`) and the `atlas-chat` CLI (`--compliance-level`).
- `/api/config` reports `features.compliance_level_required`, and `/api/compliance-levels` reports the resolved `default_level`.

If no `compliance-levels.json` can be loaded, the UI has no levels to offer and every turn is refused, so make sure the file is in place before enabling the requirement.

## What the Selector Does in the UI

Choosing a level applies the same rule everywhere -- the tools and prompts panels, the persona picker, the data sources panel and the model picker: a resource is shown only when the selected level is one of its allowed data classifications. A resource with none is hidden while a level is selected. With "All Levels", nothing is filtered. Personas carry a single `compliance_level` and must match the selected level. The built-in `atlas` tools are exempt, as above. In the data sources panel, a source whose RAG server is not approved for the level is shown disabled, because the server will not query it.

Selections follow the same rule, so what is sent is always what the panels show:

- Tool, prompt and data source selections the level excludes are deselected, and a selection the UI cannot place yet (while the configuration is still loading) is held back from the message rather than sent unchecked -- on a level switch, when the page loads with a saved level, and when a workspace is restored. Selections the level allows are kept.
- An active persona or MCP prompt the level excludes is cleared.
- If the selected model is not approved for the level, the UI switches to the first approved model and says so. If no allowed model exists, the model button is highlighted with a warning, the model picker explains that no models match, and sending is refused until you pick an allowed model or change the level.
- A saved level that the deployment no longer defines is dropped, rather than silently hiding everything. If the level definitions cannot be loaded at all, the selector stays visible with the saved level so it can still be cleared.

These rules apply both when you change the level and when the page loads with a saved level.

The server re-checks every turn (see Server-Side Enforcement), so this filtering is a convenience, not the boundary.

