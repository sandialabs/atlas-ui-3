# Changelog fragments

Add one fragment per pull request instead of editing `CHANGELOG.md`. Each PR
writes its own file, so independent PRs no longer conflict in the same region
at the top of the changelog.

## Naming

```
changes/<id>.<type>.md
```

- `<id>` — the issue or pull request number (e.g. `1002`).
- `<type>` — one of:

  | Type       | Section heading   | Use for                                             |
  |------------|-------------------|-----------------------------------------------------|
  | `breaking` | Breaking Changes  | API/config changes that require operator action     |
  | `feature`  | Features          | New user-visible capabilities                        |
  | `fix`      | Fixes             | Bug fixes                                            |
  | `security` | Security          | Security-relevant changes                            |
  | `internal` | Internal          | Refactors, tooling, CI, dependency bumps             |

Examples: `changes/1042.feature.md`, `changes/1047.fix.md`,
`changes/1051.security.md`.

## Content

One or two sentences describing the change for a release-notes reader. Markdown
is allowed; multi-line bodies are indented under the generated bullet.

```markdown
Fixed OAuth token refresh when accessing MCP servers.
```

## How it ships

At release time `scripts/changelog_fragments.py collect` composes every
fragment into the `## [Unreleased]` section of `CHANGELOG.md`, grouped by type,
and deletes the consumed files. `scripts/release_bump.py apply` then renames
that section to `## [X.Y.Z] - YYYY-MM-DD` as usual. On a stabilization branch
whose version section already exists, target it directly with
`collect --section X.Y.Z`.

CI validates the filename and type, requires a normal PR to add a fragment,
and rejects direct edits to `CHANGELOG.md` (release branches are exempt). Do
not edit `CHANGELOG.md` yourself.

See [docs/developer/release-process.md](../docs/developer/release-process.md)
for the full release runbook.
