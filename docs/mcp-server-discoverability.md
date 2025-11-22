# MCP Server Discoverability Feature

## Overview

The `allow_discovery` feature enables MCP servers to be visible in the marketplace to users even if they don't have access. This allows users to:
- See that a capability exists
- View server overview information (description, author, short description)
- Access contact information (help_email) to request access
- Know which groups are required for access

## Configuration

To make an MCP server discoverable, add `"allow_discovery": true` to its configuration in `mcp.json`:

```json
{
  "restricted_server": {
    "command": ["python", "mcp/restricted_server/main.py"],
    "cwd": "backend",
    "groups": ["admin", "privileged_users"],
    "allow_discovery": true,
    "description": "Advanced analytics server with restricted access",
    "author": "Analytics Team",
    "short_description": "Advanced data analysis tools",
    "help_email": "analytics-access@example.com",
    "compliance_level": "SOC2"
  }
}
```

### Configuration Fields

- **allow_discovery** (boolean, default: `false`): When `true`, users without access can see the server in the marketplace with limited information
- **description** (string): Full description shown to all users who can discover the server
- **author** (string): Author/team name shown to discoverable users
- **short_description** (string): Brief description shown in marketplace cards
- **help_email** (string): Contact email for requesting access (link text changes to "Request Access" for discoverable servers)
- **groups** (array): Required groups for access (shown to users who can discover but not access the server)

## Behavior

### For Users with Access
- Server appears normally in the marketplace
- Can select/enable the server
- See all tools and prompts
- Can use the server's functionality

### For Users without Access (when allow_discovery=true)
- Server appears in the marketplace with:
  - Lock icon and "No Access" badge
  - Server name, description, author
  - Required groups list
  - "Request Access" link (if help_email is provided)
- Cannot select or enable the server
- Tools and prompts are hidden
- Server is grayed out and non-clickable

### For Users without Access (when allow_discovery=false or not set)
- Server is completely hidden from the marketplace
- User has no indication the server exists

## Use Cases

1. **Access Request Workflow**: Users can discover available capabilities and contact the appropriate team to request access
2. **Capability Awareness**: Organizations can make users aware of available tools without granting blanket access
3. **Self-Service Discovery**: Users can explore what's available and understand requirements for access

## Security Considerations

- Only server metadata is exposed to unauthorized users (description, author, contact)
- Tools, prompts, and actual functionality remain hidden
- Server remains completely inaccessible without proper group membership
- The feature is opt-in (default: false) to maintain security by default

## API Response

The `/api/config` endpoint returns discoverable servers in a separate `discoverable_servers` array:

```json
{
  "tools": [...],
  "prompts": [...],
  "discoverable_servers": [
    {
      "server": "restricted_server",
      "description": "Advanced analytics server with restricted access",
      "author": "Analytics Team",
      "short_description": "Advanced data analysis tools",
      "help_email": "analytics-access@example.com",
      "groups": ["admin", "privileged_users"],
      "compliance_level": "SOC2",
      "is_discoverable": true,
      "has_access": false
    }
  ]
}
```

## Frontend Display

In the marketplace panel, discoverable servers are displayed with:
- Grayed out appearance (reduced opacity)
- Lock icon instead of selection checkbox
- "No Access" badge in orange
- Required groups information
- "Request Access" mailto link (if help_email provided)
- No tool/prompt preview
