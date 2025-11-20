# Custom Prompting via MCP

This document describes the custom prompting functionality that allows MCP servers to expose specialized system prompts that modify the AI's behavior and expertise.

## Overview

The custom prompting system allows MCP servers to provide predefined prompts that can be applied to the AI assistant to make it adopt specific personalities, expertise areas, or behavioral patterns. When a user selects tools from a prompt-enabled MCP server, the system automatically applies the relevant system prompt to the conversation.

## How It Works

1. **MCP Prompt Discovery**: The system discovers prompts from MCP servers alongside tools
2. **Prompt Selection**: When tools from a prompt server are selected, the system identifies applicable prompts
3. **System Prompt Injection**: For the first message in a conversation, relevant system prompts are injected
4. **Conversation Context**: The AI adopts the specified personality/expertise for the entire conversation

## Creating a Prompt-Enabled MCP Server

### Basic Structure

```python
from fastmcp import FastMCP
from fastmcp.prompts.prompt import Message, PromptMessage, TextContent

mcp = FastMCP("MyPromptServer")

@mcp.prompt
def expert_persona() -> PromptMessage:
    """Description of the expert persona."""
    content = """You are an expert in [domain] with deep knowledge of:
- Key area 1
- Key area 2  
- Key area 3

Provide [type] of responses with [characteristics]."""
    
    # Note: Use role="user" with embedded system instruction
    return PromptMessage(
        role="user", 
        content=TextContent(
            type="text", 
            text=f"System: {content}\n\nUser: Please adopt this expertise for our conversation."
        )
    )
```

### Example Prompts

The system includes several example prompts:

#### Financial Tech Wizard
```python
@mcp.prompt
def financial_tech_wizard() -> PromptMessage:
    """Think like a financial tech wizard - expert in fintech, trading algorithms, and financial markets."""
    # Makes the AI respond as a financial technology expert
```

#### Expert Dog Trainer  
```python
@mcp.prompt
def expert_dog_trainer() -> PromptMessage:
    """You are an expert dog trainer with years of experience in canine behavior and training."""
    # Makes the AI respond as a professional dog trainer
```

#### Creative Writer
```python
@mcp.prompt
def creative_writer() -> PromptMessage:
    """You are a creative writing expert focused on storytelling, character development, and narrative craft."""
    # Makes the AI respond as a creative writing expert
```

### Adding Tools

You can also include tools alongside prompts:

```python
@mcp.tool
def list_available_prompts() -> Dict[str, Any]:
    """List all available system prompts that can be applied to modify AI behavior."""
    return {
        "available_prompts": {...},
        "total_count": len(prompts),
        "categories": [...]
    }
```

## Configuration

Add your prompt server to `mcp.json`:

```json
{
  "prompts": {
    "command": ["python", "mcp/prompts/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Specialized system prompts for AI behavior modification"
  }
}
```

## Usage

1. **Server Selection**: Users select tools from prompt-enabled servers in the UI
2. **Automatic Application**: The system automatically detects and applies relevant prompts
3. **Conversation Start**: The prompt is applied only to the first message in a conversation
4. **Persistent Effect**: The prompt effect continues throughout the conversation

## Technical Implementation

### Message Processing Flow

1. User sends first message with prompt server tools selected
2. `MessageProcessor._get_custom_system_prompt()` is called
3. System discovers available prompts from selected servers
4. Relevant prompts are retrieved and combined
5. System message is injected before the user message
6. LLM processes with modified context

### Key Components

- **`mcp_client.py`**: Extended with `discover_prompts()` and `get_prompt()` methods
- **`message_processor.py`**: Enhanced with `_get_custom_system_prompt()` logic
- **`mcp/prompts/main.py`**: Example prompt server implementation

## Best Practices

### Prompt Design
- Be specific about the expertise domain
- Include relevant context and background
- Specify the type of responses expected
- Keep prompts focused but comprehensive

### Server Organization
- Group related prompts in the same server
- Provide descriptive names and documentation
- Include a tool to list available prompts
- Consider prompt categories for organization

### Testing
- Test prompts with various question types
- Verify prompt application in conversations
- Check for appropriate expertise demonstration
- Ensure prompts don't conflict with each other

## Examples in Action

### Financial Tech Conversation
```
User: [selects prompts_financial_tech_wizard tool]
User: "How should I evaluate a fintech startup?"

AI: "As a financial technology expert, I'd recommend evaluating fintech startups across several key dimensions:

1. **Regulatory Compliance**: Ensure the startup understands and complies with relevant financial regulations (PCI DSS, KYC, AML, etc.)...
```

### Dog Training Consultation  
```
User: [selects prompts_expert_dog_trainer tool]
User: "My puppy won't stop biting during play"

AI: "As an experienced dog trainer, this is a very common and solvable issue. Puppy biting during play is natural behavior that needs to be redirected using positive reinforcement techniques:

1. **Bite Inhibition Training**: When your puppy bites too hard during play, immediately stop and say 'ouch' in a high-pitched voice...
```

## Troubleshooting

### Common Issues

1. **Prompts not discovered**: Check server configuration and MCP client logs
2. **Prompts not applied**: Verify tool selection includes prompt server tools  
3. **Validation errors**: Ensure prompts use correct `role` and `content` format
4. **Multiple prompts**: System combines multiple prompts when multiple servers selected

### Debugging

- Check `discover_prompts()` logs for server connections
- Verify `get_available_prompts_for_servers()` returns expected prompts
- Monitor `_get_custom_system_prompt()` execution in message processing
- Review conversation message history for proper system prompt injection