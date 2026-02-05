# Elicitation Demo MCP Server

This MCP server demonstrates **user elicitation** capabilities introduced in FastMCP 2.10.0+. Elicitation allows tools to pause execution and request structured input from users during tool execution, rather than requiring all inputs upfront.

## Overview

User elicitation enables interactive workflows where tools can:
- Request missing or clarifying information mid-execution
- Collect complex data step-by-step across multiple prompts
- Ask for user approval or confirmation
- Adapt behavior based on user responses

## Available Tools

### Basic Elicitation Types

1. **`get_user_name`** - String Input
   - Demonstrates basic string elicitation
   - Asks for user's name and returns a greeting

2. **`pick_a_number`** - Integer Input  
   - Demonstrates numeric elicitation
   - Asks for a number between 1-100 and performs calculation

3. **`confirm_action`** - Boolean Input
   - Demonstrates boolean confirmation
   - Asks yes/no question and proceeds based on response

4. **`set_priority`** - Enum Input (Python Enum)
   - Demonstrates enum-based selection  
   - User chooses from predefined priority levels (low/medium/high)

5. **`choose_option`** - Enum Input (String List)
   - Demonstrates string literal selection
   - User picks favorite color from list of options

### Advanced Elicitation

6. **`create_task`** - Structured Multi-field Form
   - Demonstrates structured data collection
   - Collects task with title, description, priority, and due date in single form

7. **`multi_turn_survey`** - Multi-turn Elicitation
   - Demonstrates progressive data collection
   - Asks 4 questions sequentially: name, age, favorite food, satisfaction rating
   - Can be cancelled at any step

8. **`approve_deletion`** - Approval-only (No Data)
   - Demonstrates confirmation without additional data
   - Simple approve/decline for sensitive actions

## Usage Examples

### In Atlas UI Chat

After the elicitation_demo server is enabled, you can test it with prompts like:

```
"Get my name using the elicitation demo"
"Ask me to pick a number"
"Create a task by asking me for the details"
"Run a survey about my preferences"
"Test the approval process for deletion"
```

### Expected User Experience

When a tool calls `ctx.elicit()`:

1. **Dialog Appears**: A modal dialog pops up with the prompt message
2. **Form Fields**: Input fields are shown based on the requested type:
   - Text boxes for strings
   - Number inputs for integers/numbers
   - Checkboxes for booleans
   - Dropdowns for enums
   - Multiple fields for structured types
3. **User Actions**:
   - **Accept**: Submit the provided data (only enabled if required fields are filled)
   - **Decline**: Skip providing data (tool can handle this case)
   - **Cancel**: Abort the entire operation

## Technical Details

### Response Schema

The tool specifies what type of data it expects using JSON Schema. Atlas UI parses this schema to render appropriate form fields:

- **Scalar types**: Wrapped in object with `value` field, then unwrapped for tool
- **Enums**: Rendered as dropdown selects
- **Structured**: Multiple fields in a form
- **None/Empty**: Approval-only, no data fields shown

### Elicitation Actions

Tools receive a response with:
- `action`: "accept" | "decline" | "cancel"  
- `data`: The user's input (only present on "accept")

### Timeouts

Elicitation requests timeout after 5 minutes of waiting. The tool receives a "cancel" response if timeout occurs.

## Configuration

This server is configured in `config/defaults/mcp.json` (or an overrides directory set via `APP_CONFIG_OVERRIDES`):

```json
{
  "elicitation_demo": {
    "command": ["python", "mcp/elicitation_demo/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "description": "Demonstrates MCP user elicitation capabilities...",
    "compliance_level": "Public"
  }
}
```

## Development

To run the server standalone for testing:

```bash
cd backend
python mcp/elicitation_demo/main.py
```

The FastMCP framework will display available tools and their schemas.

## References

- [FastMCP Elicitation Documentation](https://gofastmcp.com/clients/elicitation)
- [MCP Specification - Elicitation](https://spec.modelcontextprotocol.io/)
- FastMCP Version: 2.10.0+

## Example Tool Implementation

```python
from fastmcp import FastMCP, Context
from dataclasses import dataclass

mcp = FastMCP("My Server")

@dataclass
class UserInfo:
    name: str
    age: int

@mcp.tool
async def collect_info(ctx: Context) -> str:
    """Collect user information interactively."""
    result = await ctx.elicit(
        message="Please provide your information",
        response_type=UserInfo
    )
    
    if result.action == "accept":
        user = result.data
        return f"Hello {user.name}, age {user.age}!"
    elif result.action == "decline":
        return "Information not provided"
    else:  # cancel
        return "Operation cancelled"
```

## Support

For issues or questions about elicitation:
- Check Atlas UI documentation in `/docs` folder
- Review FastMCP elicitation docs at https://gofastmcp.com
- Report bugs via GitHub issues
