# Agent Skills

Last updated: 2026-03-18

## Overview

Agent skills are reusable instruction packages that give the agent domain-specific expertise. When a skill is selected and agent mode is active, the skill's prompt is injected into the system prompt, directing the agent to behave as a specialist.

Skills follow the [agentskills.io specification](https://agentskills.io/specification) format and are configured in `config/skills.json`.

## Configuration

Skills are defined in a flat JSON file where each key is the skill identifier:

```json
{
  "pubmed_researcher": {
    "name": "PubMed Researcher",
    "description": "Searches and analyzes biomedical literature from PubMed",
    "version": "1.0.0",
    "author": "Your Name",
    "help_email": "support@example.com",
    "prompt": "You are an expert scientific research assistant specialized in querying and analyzing biomedical literature from PubMed. When given a research question:\n\n1. Use the available tools to search PubMed for relevant papers\n2. Analyze and synthesize findings across multiple sources\n3. Provide evidence-based answers with proper citations\n4. Highlight conflicting findings or limitations\n5. Suggest follow-up research directions when appropriate\n\nAlways prioritize accuracy and cite your sources clearly.",
    "required_tools": ["pubmed_search"],
    "compliance_level": "Public",
    "groups": ["users"],
    "enabled": true
  }
}
```

### Configuration Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Human-readable skill name displayed in the UI |
| `description` | string | no | Short description shown below the skill selector |
| `prompt` | string | no | Instructions injected into the system prompt when the skill is active |
| `version` | string | no | Skill version (default: `"1.0.0"`) |
| `author` | string | no | Skill author name |
| `help_email` | string | no | Contact email for support |
| `required_tools` | list | no | Tool names the skill is designed to work with (informational only) |
| `compliance_level` | string | no | Compliance level (e.g., `"Public"`, `"Internal"`) |
| `groups` | list | no | User groups that can access the skill (empty = all users) |
| `enabled` | bool | no | Whether the skill appears in the UI (default: `true`) |

## File Location

The skills config file is loaded from these locations in priority order:

1. `config/skills.json` (user config directory, relative to project root)
2. `$APP_CONFIG_DIR/skills.json` (if `APP_CONFIG_DIR` is set)
3. `atlas/config/skills.json` (package defaults — empty by default)

To add skills, create `config/skills.json` in your project root.

## Using Skills in Agent Mode

1. Open the **Agent Settings** panel (click the robot/agent icon in the chat header)
2. Enable **Agent Mode**
3. Select a skill from the **Agent Skill** dropdown
4. The skill's instructions will be active for the next chat message

When a skill is selected, its `prompt` field is appended to the base system prompt with a blank line separator, giving the agent domain-specific guidance while preserving the base system behavior.

## Access Control

Skills support group-based access control via the `groups` field:

```json
{
  "internal_analyst": {
    "name": "Internal Data Analyst",
    "groups": ["data-team", "admin"],
    "prompt": "...",
    "enabled": true
  }
}
```

If `groups` is empty or not set, the skill is available to all authenticated users. If set, only users in one of the listed groups will see the skill.

## Environment Variable Override

The skills config filename can be customized:

```bash
SKILLS_CONFIG_FILE=my-skills.json
```

## Example Skills

See `atlas/config/mcp-example-configs/skills-example.json` for example skill definitions including a literature researcher and data analyst.

## Troubleshooting

**Skill not appearing in the dropdown**
- Verify `enabled: true` in the skill config
- Check that the user is in one of the skill's `groups`
- Ensure the config file is valid JSON (`python3 -c "import json; json.load(open('config/skills.json'))"`)
- Restart the backend or wait for the config to reload

**Skill prompt not being applied**
- Confirm the skill's `prompt` field is non-empty
- Check backend logs for `Resolved skill '...' to prompt` message at DEBUG level
- Ensure `agent_mode` is `true` in the chat request (skills are only sent when agent mode is enabled)
