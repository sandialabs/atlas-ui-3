# Compliance and Data Security

Last updated: 2026-10-07

The compliance system is designed to prevent the unintentional mixing of data from different security environments. This is essential for organizations that handle sensitive information.

## Compliance Levels

You can assign a `compliance_level` to LLM endpoints, RAG data sources, and MCP servers. These levels are defined in `atlas/config/compliance-levels.json` (package default) and can be customized in `config/compliance-levels.json` (or `compliance-levels.json` in whatever directory `APP_CONFIG_DIR` names). A file in the user config directory replaces the package default entirely, so a custom file must list every level you use. A `compliance_level` value that names no defined level (or alias) is treated as unset and logged as a warning.

> **Upgrade note (2026-10):** before this release the levels file was never found, so validation ran permissively and any level name was accepted. Level definitions now take effect: if you tag models, MCP servers or RAG sources with names that are not in the bundled defaults (for example `CUI`), add them to `config/compliance-levels.json`, or those resources lose their level (models) or have RAG queries rejected for lacking a trusted level.

**Example:** A tool that accesses internal-only data can be marked with `compliance_level: "Internal"`, while a tool that uses a public API can be marked as `compliance_level: "Public"`.

## The Allowlist Model

The compliance system uses an explicit **allowlist**. Each compliance level defines which other levels it is allowed to interact with. This prevents data from a highly secure environment (e.g., "HIPAA") from being accidentally sent to a less secure one (e.g., "Public").

For example, a session running with a "HIPAA" compliance level will not be able to use tools or data sources marked as "Public", preventing sensitive data from being exposed.

## Enabling the Compliance Selector

Set `FEATURE_COMPLIANCE_LEVELS_ENABLED=true`. The header then shows a compliance level selector (in the overflow menu on narrow screens) listing every defined level, plus "All Levels" for no filter.

## What the Selector Does in the UI

Choosing a level applies one rule everywhere -- the tools and prompts panels, the persona picker, the data sources panel and the model picker: a resource is shown only when its `compliance_level` is in the selected level's `allowed_with` list. A resource with **no** `compliance_level` is hidden while a level is selected, because it has no declared boundary. With "All Levels", nothing is filtered.

Selections follow the same rule, so what is sent is always what the panels show:

- Tool, prompt and data source selections the level excludes are deselected -- on a level switch, when the page loads with a saved level, and when a workspace is restored. Selections the level allows are kept (for example, switching to HIPAA keeps SOC2 tools).
- An active persona the level excludes is cleared.
- If the selected model is outside the level, the UI switches to a model at that level (or another model it allows) and says so. If no allowed model exists, the model button is highlighted with a warning and the model picker explains that no models match.
- A saved level that the deployment no longer defines is dropped, rather than silently hiding everything.

MCP tool calls have no separate server-side compliance check, so this client-side filtering is what keeps an excluded tool out of a turn. RAG queries are additionally enforced on the server against the *selected model's* compliance level.

