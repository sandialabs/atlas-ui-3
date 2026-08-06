---
name: skill-authoring
description: >
  Write, validate, and install Agent Skills for Atlas. Use when the user asks how
  to create a skill, where skills live, what goes in SKILL.md frontmatter, why a
  skill is not showing up, or asks to author a new skill for Atlas.
license: MIT
metadata:
  author: Atlas
  version: "1"
---

# Authoring an Agent Skill for Atlas

Atlas follows the [Agent Skills specification](https://agentskills.io/specification),
so a skill written for Atlas also works in other Agent Skills-compatible tools.

## Layout

One directory per skill, containing at minimum a `SKILL.md`:

```
my-skill/
  SKILL.md          # required
  scripts/          # optional (Atlas does not execute these)
  references/       # optional, loaded on demand
  assets/           # optional
```

## Frontmatter

```yaml
---
name: my-skill
description: What it does, and when it should be used. Include trigger phrases.
---
```

- `name` — required, 1–64 chars, lowercase letters/digits with single hyphens, no
  leading or trailing hyphen. **Must match the directory name.**
- `description` — required, 1–1024 chars.
- `license`, `compatibility`, `metadata`, `allowed-tools` — optional.

The `description` is the only part of your skill that is always in context, so it
decides whether the skill is ever used. Say both what the skill does *and* when it
applies, and include the words a user would actually type.

## Where to put it

Atlas scans these roots, later ones overriding earlier ones by skill name:

1. `atlas/config/skills/` — shipped with Atlas
2. `~/.atlas/skills/`, `~/.claude/skills/`, `~/.agents/skills/` — per user
3. `.atlas/skills/`, `.claude/skills/`, `.agents/skills/` under the project root

Set `SKILLS_PATHS` to replace that list with your own directories.

## Body

Write instructions for an agent, not documentation for a human. Keep `SKILL.md`
under about 500 lines and move long material into `references/`, telling the
agent when to open it.

## If a skill does not appear

- The file must be named exactly `SKILL.md`.
- `name` must equal the directory name.
- `FEATURE_SKILLS_ENABLED` must be `true`.
- Start a new chat session — the skill list is rescanned per session.
- Check the server log for `Skipping invalid skill` warnings.
