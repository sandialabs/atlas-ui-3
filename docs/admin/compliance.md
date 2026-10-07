# Compliance and Data Security

Last updated: 2026-10-07

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

## The Rule

The active conversation classification -- the level selected in the header -- is the authority. A component may be used only when that classification is a member of its `allowed_data_classifications`:

| Active classification | Model X (`UUR, ITAR, ECI`) | Model Y (`UUR`) | google-search (`UUR`) | internal-search (`UUR, ITAR, ECI`) |
|---|---|---|---|---|
| UUR  | yes | yes | yes | yes |
| ITAR | yes | no  | no  | yes |
| ECI  | yes | no  | no  | yes |

- **Deny by default.** A component that declares no classifications (neither `allowed_data_classifications` nor a legacy `compliance_level`), or an empty list, is approved for no classified session. This matters most for MCP servers, which can be data egress points.
- **No implication between levels.** `allowed_with` in `compliance-levels.json` no longer grants access across levels: an ITAR session never reaches a UUR-only model or tool because ITAR "allows" UUR. List every classification a component may receive on the component itself.
- With no level selected ("All Levels", only offered when a level is not required), there is no classification to protect and nothing is filtered. Use `FEATURE_COMPLIANCE_LEVEL_REQUIRED` (below) to remove that choice.

## Server-Side Enforcement

The rule is enforced on the server, not only in the UI, so a stale bundle, the CLI, the Python client or a hand-crafted WebSocket client cannot bypass it. Before a chat turn runs, the server checks the selected model, the MCP server behind every selected tool, and every selected data source against the active classification. If any is not approved, the turn is refused with a message naming them, before the session is touched or anything is called. If the check itself cannot run (a broken configuration lookup), a classified turn is refused rather than run unchecked.

A RAG backend's discovery can also declare classifications per corpus (narrower than its server's). For a classified turn with selected data sources, the server runs discovery at the active level and refuses any selected corpus it does not offer; a corpus that declares nothing inherits its server's classifications. RAG queries are checked again at query time against the active classification, which also covers `atlas_search` calls the model makes during a turn; discovery for the `atlas_search` tool only offers sources approved for it. The built-in `atlas` tools (canvas, sleep, search, discover sources) run in-process and are exempt from the tool check; search reaches only sources that pass their own checks.

## Migrating from `compliance_level`

`compliance_level` is deprecated but still read:

1. A component with only `compliance_level: X` is treated as `allowed_data_classifications: [X]`.
2. When both are set, `allowed_data_classifications` wins and a deprecation warning is logged at load. Remove `compliance_level` once you have migrated.
3. For a LiteLLM gateway, the most specific declaration wins: the allowlisted model's `allowed_data_classifications`, then its `compliance_level`, then the gateway's `allowed_data_classifications`, then the gateway's `compliance_level`.
4. HTTP RAG discovery may return `allowed_data_classifications` per source alongside `compliance_level`; MCP RAG resources may return `allowedDataClassifications`. A resource that declares nothing inherits its server's classifications.

> **Behavior change:** before this release, a level's `allowed_with` list let a session use components at other levels (the bundled HIPAA level allowed SOC2 components), and a component with no level was usable from any session on the server side. Both now fail closed. To keep a component usable at several levels, list them all in its `allowed_data_classifications`. `allowed_with` is still accepted in `compliance-levels.json` (and returned by `/api/compliance-levels`) for compatibility, but it no longer affects access.

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

