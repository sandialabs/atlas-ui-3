# Compliance and Data Security

Last updated: 2026-10-07

The compliance system is designed to prevent the unintentional mixing of data from different security environments. This is essential for organizations that handle sensitive information.

## Compliance Levels

You can assign a `compliance_level` to LLM endpoints, RAG data sources, and MCP servers. These levels are defined in `atlas/config/compliance-levels.json` (package default) and can be customized in `config/compliance-levels.json` (or `compliance-levels.json` in whatever directory `APP_CONFIG_DIR` names). A file in the user config directory replaces the package default entirely, so a custom file must list every level you use. A `compliance_level` value that names no defined level (or alias) is treated as unset and logged as a warning.

> **Upgrade note (2026-10):** before this release the levels file was never found, so validation ran permissively and any level name was accepted. Level definitions now take effect, so define every level name you use in `config/compliance-levels.json` (for example `CUI` is not in the bundled defaults). An undefined name on an LLM model is cleared at load, so that model has no level: the UI filter hides it, and server-side RAG enforcement is off for turns using it. An undefined name on a RAG source is logged as a warning at load but is **not** enforced -- the server treats a level it cannot resolve as accessible -- so an undefined level does not protect a corpus.

**Example:** A tool that accesses internal-only data can be marked with `compliance_level: "Internal"`, while a tool that uses a public API can be marked as `compliance_level: "Public"`.

## The Allowlist Model

The compliance system uses an explicit **allowlist**. Each compliance level defines which other levels it is allowed to interact with. This prevents data from a highly secure environment (e.g., "HIPAA") from being accidentally sent to a less secure one (e.g., "Public").

For example, a session running with a "HIPAA" compliance level will not be able to use tools or data sources marked as "Public", preventing sensitive data from being exposed.

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

Choosing a level applies one rule everywhere -- the tools and prompts panels, the persona picker, the data sources panel and the model picker: a resource is shown only when its `compliance_level` is in the selected level's `allowed_with` list. A resource with **no** `compliance_level` is hidden while a level is selected, because it has no declared boundary. With "All Levels", nothing is filtered. The built-in `atlas` tools (canvas, sleep, search, discover sources) are exempt: they run in-process, and search only reaches sources that pass their own compliance checks.

Selections follow the same rule, so what is sent is always what the panels show:

- Tool, prompt and data source selections the level excludes are deselected, and a selection the UI cannot place yet (while the configuration is still loading) is held back from the message rather than sent unchecked -- on a level switch, when the page loads with a saved level, and when a workspace is restored. Selections the level allows are kept (for example, switching to HIPAA keeps SOC2 tools).
- An active persona or MCP prompt the level excludes is cleared.
- If the selected model is outside the level, the UI switches to a model at that level (or another model it allows) and says so. If no allowed model exists, the model button is highlighted with a warning, the model picker explains that no models match, and sending is refused until you pick an allowed model or change the level.
- A saved level that the deployment no longer defines is dropped, rather than silently hiding everything. If the level definitions cannot be loaded at all, the selector stays visible with the saved level so it can still be cleared.

These rules apply both when you change the level and when the page loads with a saved level.

MCP tool calls have no separate server-side compliance check, so this client-side filtering is what keeps an excluded tool out of a turn. RAG queries are additionally enforced on the server against the *selected model's* compliance level.

