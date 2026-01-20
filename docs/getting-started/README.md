# Getting Started with Atlas UI 3

Last updated: 2026-01-19

Welcome to Atlas UI 3! This guide will help you get the application up and running.

## Quick Links

- [Installation Guide](installation.md) - How to install and run Atlas UI 3
- [Interactive Tool Elicitation](elicitation.md) - Learn about tool elicitation features

## Next Steps

Once you have the application running:

- **For Administrators**: See the [Administrator's Guide](../admin/README.md) to learn about configuration, security, and deployment
- **For Developers**: See the [Developer's Guide](../developer/README.md) to learn about the architecture and how to extend the system

## Using Atlas UI 3

### Interactive Tool Elicitation

Atlas UI 3 supports **interactive tool elicitation**, a powerful feature that allows MCP tools to pause execution and request additional information from users during tool execution.

#### How Elicitation Works

When using MCP tools, you may encounter dialogs that request additional input. This enables more interactive and flexible workflows where tools can:

- Request missing or clarifying information mid-execution
- Collect complex data step-by-step across multiple prompts
- Ask for approval before performing sensitive operations
- Adapt behavior based on your responses

#### Elicitation Dialog Examples

**Creating a Task**

Tools can request structured information like task details through an interactive form:

![Create Task Elicitation](../readme_img/elicitation-demo-create-task.png)

**Choosing Options**

Tools can present you with predefined options to select from:

![Choose Option Elicitation](../readme_img/elicitaton-demo-choose-option.png)

#### Try It Out

To experience elicitation in action:

1. Enable the `elicitation_demo` MCP server in the admin panel
2. Try prompts like:
   - "Create a task by asking me for the details"
   - "Ask me to pick a favorite color"
   - "Get my name and age through a survey"
   - "Test the approval process for a deletion action"

The tool will pause execution and display an interactive dialog for collecting the requested information.
