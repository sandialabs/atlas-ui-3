# Preconfigured personas (default system prompts)

Atlas can offer users a set of ready-made system prompts — "personas" — that an
admin defines as markdown files. No code changes, no database entries, and no
MCP prompt server: drop a `.md` file in the personas folder and it appears in
every allowed user's prompt picker after the next server restart.

## Where the files live

The first directory that contains any persona `.md` files wins:

1. `PERSONAS_DIR` (env var), if set — absolute, or relative to the project root.
2. `<PROMPT_BASE_PATH>/personas/` (by default `config/prompts/personas/`).
3. `prompts/personas/` in the project root.
4. `atlas/config/prompts/personas/` — the packaged samples.

Because the first populated directory is authoritative, your own folder fully
replaces the packaged samples rather than being merged with them.

## File format

YAML frontmatter for the metadata, everything below it for the prompt text:

```markdown
---
name: Research Assistant          # optional; defaults to the filename
description: Careful, source-first answers
access_group: research-team       # optional; one group or a list of them
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
  Invalid YAML frontmatter is logged and the body is still used.
- The persona id comes from the filename, so renaming a file changes its id and
  users who had it selected fall back to the default prompt.

## How users pick one

The prompt selector under the chat input gains a **Personas** section. Selecting
a persona replaces the system prompt for the following turns; **Default Prompt**
switches back. Personas are read-only for users — to change one, edit the file.

## API

`GET /api/personas` returns the personas the caller may see, and
`GET /api/personas/{id}` returns one (404 if it does not exist *or* is gated
behind a group the caller is not in). Both include the prompt `content`.

## Operating notes

Files are read once at server startup, so restart Atlas after adding or editing
a persona. Parse problems are reported in the startup log with the filename.
