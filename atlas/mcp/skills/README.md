# Agent Skills MCP Server

Serves the content half of Agent Skills support in Atlas.

Atlas injects the skill **index** (each skill's `name` and `description`) into the
system prompt when `FEATURE_SKILLS_ENABLED=true`. That index deliberately does not
contain skill instructions. This server provides the tools the model uses to pull
those instructions once it has decided a skill applies — the progressive-disclosure
model from the [Agent Skills specification](https://agentskills.io/specification).

## Tools

| Tool | Purpose |
| --- | --- |
| `list_skills` | Re-list available skills (name + description). |
| `read_skill(name)` | Return the full `SKILL.md` text plus the relative paths of bundled resources. |
| `read_skill_resource(name, resource_path)` | Read one bundled file, e.g. `references/REFERENCE.md`. |

## Enabling

The server is not in the default `mcp.json`. To turn it on, copy the example config:

```bash
cp atlas/config/mcp-example-configs/mcp-skills.json /tmp/skills.json
# then merge that object into your config/mcp.json
```

Which directories are scanned is controlled by `SKILLS_PATHS`. Note that this server
reads skills regardless of `FEATURE_SKILLS_ENABLED` — that flag governs whether the
skill index is injected into the system prompt, while access to this server is
governed by its presence in `mcp.json` and its `groups` entry. Enabling the server
without the flag gives the model the reading tools but no index telling it what
exists, which is rarely what you want.

See [docs/user-guide/skills/README.md](../../../docs/user-guide/skills/README.md).

## Security

This server **only reads files**. It does not execute skill-bundled scripts, and
`read_skill_resource` refuses any path that resolves outside the skill's own
directory, so a crafted `resource_path` cannot be used to read unrelated files.
Files larger than 256 KB are refused rather than returned.

Executing code bundled in a skill's `scripts/` directory is intentionally out of
scope and would need its own sandboxing design.
