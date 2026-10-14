# Preconfigured personas

Every `.md` file in this folder becomes a system prompt users can pick from the
prompt selector, with no code changes and no MCP prompt server.

```markdown
---
name: Research Assistant          # optional; defaults to the filename
description: One-line summary     # optional; shown under the name in the picker
access_group: research-team       # optional; a group name or a list of them
allowed_data_classifications:     # optional; explicit list of levels
  - Internal
order: 10                         # optional sort hint (default 1000)
---
You are a meticulous research assistant...
```

Everything below the closing `---` is the prompt text.

- A file with **no** `access_group` is visible to every authenticated user.
- With `access_group`, the persona is only listed for users who are in at least
  one of the named groups (checked through the same authorization service as
  tools and models).
- With `allowed_data_classifications`, the persona is filtered by the user's
  active compliance level like any tool or prompt: the selected level must be
  one of the listed levels (aliases resolve). An active filter hides personas
  that list no level, an empty list, or an unrecognized level. The value must be
  a YAML list; a scalar (e.g. `allowed_data_classifications: Internal`) is
  rejected and the persona is approved for nothing. The legacy
  `compliance_level` is read as a one-element list when the new field is absent;
  when both are present the list wins.
- The persona `id` comes from the filename, so renaming a file changes its id.

Files are read at server startup. To override or extend this packaged set, point
`PERSONAS_DIR` at your own folder, or create `config/personas/` (more precisely
`<APP_CONFIG_DIR>/personas/`) in the project root — the first folder that
contains any `.md` files wins, so your folder fully replaces these samples.
