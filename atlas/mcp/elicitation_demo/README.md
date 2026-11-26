# Elicitation Demo MCP Server

This MCP server demonstrates **user elicitation**: tools that ask the user for structured input while they run, rather than requiring all inputs upfront.

It is written for FastMCP 4 and MCP `2026-07-28`, where a tool asks by returning an `InputRequiredResult` and is called again with the answers (multi round-trip requests, SEP-2322). The same tools still work for an older client: the shared `UserInput` helper falls back to `ctx.elicit()` on a handshake-era connection.

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

6. **`choose_toppings`** - Titled Choice and Multi-select
   - Demonstrates a single choice whose options have display titles, and a multi-select list
   - Asks both questions in one round trip

7. **`create_task`** - Structured Multi-field Form
   - Demonstrates structured data collection
   - Collects task with title, description, priority, and due date in single form

8. **`multi_turn_survey`** - Multi-turn Elicitation
   - Demonstrates progressive data collection
   - Asks 4 questions sequentially: name, age, favorite food, satisfaction rating
   - Each question is its own round trip; earlier answers travel in the request state
   - Can be cancelled at any step

9. **`approve_deletion`** - Yes/No Approval
   - Demonstrates approval before a destructive action
   - A single boolean field (FastMCP 4 no longer sends an empty form)

## Usage Examples

### In Atlas UI Chat

After the elicitation_demo server is enabled, you can test it with prompts like:

```
"Get my name using the elicitation demo"
"Ask me to pick a number"
"Create a task by asking me for the details"
"Run a survey about my preferences"
"Order a pizza and ask me what I want on it"
"Test the approval process for deletion"
```

### Expected User Experience

When a tool asks for input:

1. **Dialog Appears**: A modal dialog pops up with the prompt message
2. **Form Fields**: Input fields are shown based on the requested type:
   - Text boxes for strings
   - Number inputs for integers/numbers
   - Checkboxes for booleans
   - Dropdowns for single choices
   - Checkbox lists for multiple choices
   - Multiple fields for structured types
3. **User Actions**:
   - **Accept**: Submit the provided data (only enabled if required fields are filled)
   - **Decline**: Skip providing data (tool can handle this case)
   - **Cancel**: Abort the entire operation

## Technical Details

### How a Round Trip Works

On MCP `2026-07-28` a tool cannot block waiting for the user. Instead:

1. The tool returns an `InputRequiredResult` naming each question (`input_requests`) and, optionally, some opaque `request_state`.
2. Atlas shows the dialog(s) and collects the answers.
3. Atlas calls the tool again with the answers (`input_responses`) and the same `request_state`.
4. The tool runs from the top, finds its answers, and either finishes or asks the next question.

The server keeps nothing between steps 1 and 3. That is why this demo can run over STDIO, where session state is blocked: the answers given so far ride along in `request_state`, which FastMCP seals before sending and verifies on return.

Because the tool re-runs, **do not perform side effects before the last question is answered**.

### Response Schema

The tool specifies what type of data it expects using JSON Schema. Atlas UI parses this schema to render appropriate form fields:

- **Scalar types**: Wrapped in object with `value` field, then unwrapped for tool
- **Enums**: Rendered as dropdown selects; titled options (`oneOf` with `const`/`title`) show the title
- **Arrays of enums**: Rendered as a checkbox list
- **Structured**: Multiple fields in a form
- **Empty**: Confirmation only, no data fields shown (accepted from other servers; FastMCP 4 tools should ask for a `bool`)

### Elicitation Actions

Tools receive a response with:
- `action`: "accept" | "decline" | "cancel"
- `data`: The user's input (only present on "accept")

### Timeouts

An unanswered request times out after the approval timeout (5 minutes by default). The tool receives a "cancel" response if that happens.

## Configuration

This server is configured in `config/mcp.json`:

```json
{
  "elicitation_demo": {
    "command": ["python", "mcp/elicitation_demo/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "description": "Demonstrates MCP user elicitation capabilities...",
    "compliance_level": "Public"
  }
}
```

## Development

To run the server standalone for testing:

```bash
cd atlas
python mcp/elicitation_demo/main.py
```

The FastMCP framework will display available tools and their schemas.

## References

- [Atlas elicitation guide](../../../docs/developer/elicitation.md)
- [FastMCP documentation](https://gofastmcp.com)
- [MCP Specification](https://modelcontextprotocol.io/specification)
- FastMCP Version: 4.2.6+

## Example Tool Implementation

```python
from dataclasses import dataclass

from fastmcp import Context
from mcp_types import InputRequiredResult

from atlas.mcp_shared.server_factory import create_stdio_server
from atlas.mcp_shared.user_input import UserInput

mcp = create_stdio_server("My Server")

@dataclass
class UserInfo:
    name: str
    age: int

@mcp.tool
async def collect_info(ctx: Context) -> str | InputRequiredResult:
    """Collect user information interactively."""
    form = UserInput(ctx)
    result = await form.ask("info", "Please provide your information", UserInfo)
    if result is None:
        # Not answered yet: hand the question to the client.
        return form.input_required()

    if result.action == "accept":
        user = result.data
        return f"Hello {user.name}, age {user.age}!"
    elif result.action == "decline":
        return "Information not provided"
    else:  # cancel
        return "Operation cancelled"
```

Without the helper, the same tool reads `ctx.input_responses` and returns
`InputRequiredResult(input_requests={...}, request_state=...)` itself; see
`atlas/mcp_shared/user_input.py` for the details it takes care of.

## Support

For issues or questions about elicitation:
- Check Atlas UI documentation in `/docs` folder
- Review FastMCP docs at https://gofastmcp.com
- Report bugs via GitHub issues
