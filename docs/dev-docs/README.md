# Developer Documentation

This section contains comprehensive documentation for developers working on Atlas UI 3.

## Getting Started as a Developer

- **[Developer Setup](developer-setup.md)** - Complete development environment setup guide
- **[CLAUDE.md](CLAUDE.md)** - AI agent guide with architecture, workflows, and conventions (ALWAYS READ THIS FIRST)

## Architecture & Core Concepts

- **[Backend Guide](backend.md)** - Backend architecture and development
- **[Frontend Guide](frontend.md)** - Frontend development with React
- **[Configuration](configuration.md)** - Configuration system and environment setup
- **[Security Architecture](security_architecture.md)** - Security design and implementation

## Feature Development

- **[MCP Development](mcp-development.md)** - Creating and integrating MCP servers
- **[MCP Tools & Prompts v2 Guide](mcp_tools_prompts_v2_guide.md)** - Working with MCP tools and prompts
- **[File Handling](file-handling.md)** - File storage and management
- **[Custom Prompting](custom-prompting.md)** - Customizing AI prompts
- **[Advanced Features](advanced-features.md)** - Advanced functionality and features

## Technical Reference

- **[Message Types to UI](messages_types_to_ui.md)** - WebSocket message types and UI communication

## Quick Reference

### Critical Development Rules
- **ALWAYS use `uv`** for Python package management (not pip!)
- **NEVER use `uvicorn --reload`** - causes development issues
- **NEVER use `npm run dev`** - use `npm run build` instead
- **File limit: 400 lines max** per file for maintainability
- **No emojis** in code or documentation

### Quick Start Development
```bash
# One-time setup
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Quick start (recommended)
bash agent_start.sh

# Manual workflow
cd frontend && npm install && npm run build
cd ../backend && python main.py
```

## Additional Resources

- **[User Documentation](../user-docs/README.md)** - For end users
- **[Planning & Archive](../archive/README.md)** - Historical planning documents
- **[Testing Documentation](../../test/README.md)** - Testing guides and procedures
