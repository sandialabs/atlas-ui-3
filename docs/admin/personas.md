# Preconfigured personas (default system prompts)

Atlas can offer users a set of ready-made system prompts — "personas" — that an
admin defines as markdown files. No code changes, no database entries, and no
MCP prompt server: drop a `.md` file in the personas folder and it appears in
every allowed user's prompt picker after the next server restart.

## Where the files live

The first directory that contains any persona `.md` files wins:

1. `PERSONAS_DIR` (env var), if set — absolute, or relative to the project root.
2. `<APP_CONFIG_DIR>/personas/` — by default `config/personas/` in the project
   root, alongside `mcp.json` and the other admin-authored config.
3. `atlas/config/prompts/personas/` — the packaged samples.

Because the first populated directory is authoritative, your own folder fully
replaces the packaged samples rather than being merged with them.

## File format

YAML frontmatter for the metadata, everything below it for the prompt text:

```markdown
---
name: Research Assistant          # optional; defaults to the filename
description: Careful, source-first answers
access_group: research-team       # optional; one group or a list of them
allowed_data_classifications:     # optional; explicit allowlist of levels
  - Internal
  - SOC2
order: 10                         # optional sort hint (default 1000)
---
You are a meticulous research assistant.

- Answer from evidence, and say which source you used.
- State uncertainty plainly instead of hedging everything equally.
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | no | Label in the picker. Defaults to a title-cased filename. |
| `description` | no | One-line subtitle under the name. |
| `access_group` | no | Group name, or list of names. Omit to show to everyone. |
| `allowed_data_classifications` | no | Explicit allowlist of compliance levels (aliases canonicalize). Omit for no level. |
| `compliance_level` | no | Deprecated single-level form of the field above. |
| `order` | no | Sort hint; lower sorts first, ties break on name. |
| `id` | no | Stable identifier. Defaults to a slug of the filename. |

Notes:

- A file with **no** `access_group` is visible to every authenticated user.
  With one, the persona is listed only for users in at least one of the named
  groups, checked through the same authorization service as tools and models —
  and if that check errors, the persona is hidden (fail closed).
- `README.md` / `index.md`, dotfiles, and files starting with `_` are ignored,
  so you can document a folder in place.
- A file with frontmatter but no prompt text is skipped, with a log warning.
  Invalid YAML frontmatter — or frontmatter that is not a mapping — is logged
  and the **file is skipped** (fail closed): a typo must not silently drop an
  `access_group` and publish a gated persona to everyone.
- Every detected problem is printed at startup as **one clear line per persona**
  that names the file and what was wrong (see [Startup diagnostics](#startup-diagnostics)).
  A misconfigured persona is skipped or narrowed, never silently dropped.
- The persona id comes from the filename, so renaming a file changes its id and
  users who had it selected fall back to the default prompt.
- With `FEATURE_COMPLIANCE_LEVELS_ENABLED` on, personas are filtered by the
  current compliance context exactly like MCP tools and prompts. The rule is the
  same one applied to models, MCP servers and RAG sources (issue #1032): the
  selected level must be a member of the persona's
  `allowed_data_classifications` after alias resolution. A persona with **no**
  classifications (or an empty list) is hidden while a filter is active. The
  server re-checks the same rule when resolving the turn's `persona_id`, so a
  stale selection or hand-crafted client falls back to the default prompt.
  Changing the filter clears an active persona the new context excludes. Level
  names are validated on load: aliases canonicalize and an unknown name is
  dropped with a warning naming that entry, which can only narrow access. With
  the feature off, classifications are inert metadata.

### Classification allowlist and migration from `compliance_level`

`allowed_data_classifications` is an **explicit allowlist**, not a hierarchy:
the persona is usable at each listed level and nowhere else. A level's
`allowed_with` in `compliance-levels.json` never widens a persona's access.

The legacy single-value `compliance_level` still works. A persona written as:

```markdown
---
compliance_level: CUI
---
```

is interpreted exactly as `allowed_data_classifications: [CUI]`. When **both**
fields appear, `allowed_data_classifications` wins (the deprecated field is
ignored for access decisions, and a warning is logged) — including when the new
value is malformed, so a bad list never falls back to broader legacy access.
Because the frontmatter is hand-authored, `allowed_data_classifications` must be
a YAML list; a scalar such as `allowed_data_classifications: CUI` is rejected
(fail closed), unlike elsewhere in the config. To migrate, replace the single
value with a list — before:

```markdown
---
compliance_level: CUI
---
```

after:

```markdown
---
allowed_data_classifications:
  - CUI
  - CUI_no_ECI_no_ITAR_no_PII
---
```

A missing or empty allowlist — and any typo or non-list value — makes the
persona unavailable in a classified session (fail closed) rather than silently
broadening access.

Personas are independent of the custom-prompt library (`FEATURE_CUSTOM_PROMPTS_ENABLED`)
and of chat history: they need no per-user storage, so they work with those
features off. The client sends only the persona **id**; the server resolves the
text from its own folder and re-checks the access group, so a hand-crafted
client cannot select a persona it was never offered, nor substitute its own text.

## How users pick one

The prompt selector under the chat input gains a **Personas** section. Selecting
a persona replaces the system prompt for the following turns; **Default Prompt**
switches back. Personas are read-only for users — to change one, edit the file.

## API

`GET /api/personas` returns the personas the caller may see — metadata plus a
short server-computed `preview`, not the full prompt body (personas can be up
to 100k chars each, and the picker only shows two lines).
`GET /api/personas/{id}` returns one persona with its full `content` (404 if it
does not exist *or* is gated behind a group the caller is not in).

## Startup diagnostics

Personas are read once at server startup, so restart Atlas after adding or
editing a persona. The loader validates every file and prints one line per
persona for each problem it finds, then a `Loaded N preconfigured persona(s)`
summary, so a skipped or narrowed persona is never silent. For example:

```
Preconfigured personas: 3 configuration problem(s) detected.
Persona broken.md: invalid YAML frontmatter at line 2 (expected ',' or ']', but got '<stream end>'), skipping the file
Persona analyst.md: allowed_data_classifications entry 'NoSuchLevel' is not a configured data classification; dropping it so the persona is not approved for that level.
Persona analyst.md: ignored 1 non-string or empty allowed_data_classifications entry(ies); quote each level name on its own '- Level' line.
Loaded 4 preconfigured persona(s)
```

Embedded line breaks in a problem (a PyYAML error, say) are collapsed, and each
problem is one physical log line. A file whose id duplicates one already loaded
is reported too, so a shadowed file is not silently ignored. The same problems
are available programmatically on `PersonaLibrary.diagnostics` (one
`PersonaDiagnostic` with `source`, `level` and `message` per problem), which is
what the startup report is built from; it is refreshed on every `reload()`, even
if that load fails.
