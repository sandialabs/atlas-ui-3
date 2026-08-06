# Agent Skills

Atlas can discover **Agent Skills** — self-contained folders of instructions that
extend what the assistant knows how to do — and make them available to the model.

Atlas implements the [Agent Skills specification](https://agentskills.io/specification),
so skills written for Atlas work in other Agent Skills-compatible tools, and skills
you already have for those tools work in Atlas without modification.

## How it works

Skills use *progressive disclosure*, which keeps the cost of having many skills low:

1. **Always in context** — each skill's `name` and `description` (roughly 100 tokens
   per skill) are appended to the system prompt.
2. **On demand** — when the model decides a skill applies, it reads the full
   `SKILL.md` body using the `read_skill` tool.
3. **As needed** — bundled files under `references/` or `assets/` are read only if
   the skill's instructions call for them.

Only step 1 is automatic. Steps 2 and 3 require the bundled **skills MCP server**
(see [Enabling](#enabling) below); without it the model sees which skills exist but
cannot read their instructions.

## Enabling

```bash
# .env
FEATURE_SKILLS_ENABLED=true
```

Then enable the skills MCP server so the model can actually read skill bodies, by
merging `atlas/config/mcp-example-configs/mcp-skills.json` into your `config/mcp.json`.

Both are off by default.

## Where skills live

By default Atlas scans these roots, in order. A skill in a later root **overrides** a
same-named skill from an earlier one; skills whose names do not collide are merged.

| Precedence | Root | Purpose |
| --- | --- | --- |
| 1 (lowest) | `atlas/config/skills/` | Skills shipped with Atlas |
| 2 | `~/.atlas/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | Per-user, all projects |
| 3 (highest) | `.atlas/skills/`, `.claude/skills/`, `.agents/skills/` under the project root | Per-project, shared via git |

The `.claude/` and `.agents/` locations are the conventions used by Claude Code and
other agents-compatible tools. Atlas reads them so you do not have to keep duplicate
copies of a skill.

To use different directories entirely, set `SKILLS_PATHS`. This **replaces** the
defaults above:

```bash
SKILLS_PATHS=/opt/atlas/skills:/home/me/my-skills
```

Entries are separated by the OS path separator or by commas, and later entries win on
name collisions.

## Writing a skill

One directory per skill, containing at minimum a `SKILL.md`:

```
my-skill/
  SKILL.md          # required
  scripts/          # optional
  references/       # optional
  assets/           # optional
```

```markdown
---
name: my-skill
description: What this does, and when to use it. Include the words a user would type.
---

# My Skill

Step-by-step instructions for the agent.
```

Required frontmatter:

- **`name`** — 1–64 characters, lowercase letters/digits with single hyphens, no
  leading or trailing hyphen. Must match the directory name.
- **`description`** — 1–1024 characters.

Optional: `license`, `compatibility`, `metadata`, `allowed-tools`.

The `description` is the only part of a skill that is always in context, so it alone
determines whether the skill is ever used. Describe both *what* it does and *when* it
applies.

Atlas ships an example skill, `skill-authoring`, in `atlas/config/skills/`.

## Reloading

The skill list is rescanned when a **new chat session starts**. Add or edit a skill on
disk, start a new chat, and it is picked up — no server restart required.

## Limitations

- **Skill scripts are never executed.** Files under a skill's `scripts/` directory are
  discovered and can be read, but Atlas will not run them. Executing skill-bundled code
  needs a sandboxing design and is tracked separately.
- **No per-group authorization yet.** Every discovered skill is offered to every user.
  Do not put access-controlled content in a skill.
- **The index scales with skill count.** Every skill's description is in the system
  prompt on every turn. This is fine for tens of skills; beyond that, expect to trim.

## Troubleshooting

A skill is not showing up:

1. The file must be named exactly `SKILL.md`.
2. `name` in the frontmatter must equal the directory name.
3. `FEATURE_SKILLS_ENABLED` must be `true`.
4. Start a **new** chat session.
5. Check the server log for `Skipping invalid skill` — it names the file and the reason.
