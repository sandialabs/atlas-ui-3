# Documentation Map

This file provides a complete overview of all documentation in the Atlas UI 3 repository, organized by category.

## Quick Navigation

- **New to Atlas UI 3?** Start with [docs/user-docs/quick-start.md](user-docs/quick-start.md)
- **Want to contribute?** Read [docs/dev-docs/CLAUDE.md](dev-docs/CLAUDE.md) first, then [docs/dev-docs/developer-setup.md](dev-docs/developer-setup.md)
- **Looking for historical context?** Check [docs/archive/](archive/)

## Documentation Structure

```
docs/
├── user-docs/          # For end users and getting started
├── dev-docs/           # For developers and contributors
├── archive/            # Historical planning and design documents
└── planning/           # Active refactoring and feature plans
```

## User Documentation (docs/user-docs/)

Documentation for users who want to get started and use the application.

- **[README.md](user-docs/README.md)** - User documentation hub
- **[quick-start.md](user-docs/quick-start.md)** - Quick start guide for Docker and local development
- **[overview.md](user-docs/overview.md)** - What is Atlas UI 3 and what can it do
- **[integration_guide.md](user-docs/integration_guide.md)** - How to integrate with existing systems

## Developer Documentation (docs/dev-docs/)

Comprehensive documentation for developers working on Atlas UI 3.

### Getting Started
- **[README.md](dev-docs/README.md)** - Developer documentation hub
- **[CLAUDE.md](dev-docs/CLAUDE.md)** - **MUST READ FIRST** - Complete architecture, workflows, and conventions
- **[developer-setup.md](dev-docs/developer-setup.md)** - Development environment setup

### Architecture & Core Concepts
- **[backend.md](dev-docs/backend.md)** - Backend architecture and development
- **[frontend.md](dev-docs/frontend.md)** - Frontend development with React
- **[configuration.md](dev-docs/configuration.md)** - Configuration system and environment setup
- **[security_architecture.md](dev-docs/security_architecture.md)** - Security design and implementation

### Feature Development
- **[mcp-development.md](dev-docs/mcp-development.md)** - Creating and integrating MCP servers
- **[mcp_tools_prompts_v2_guide.md](dev-docs/mcp_tools_prompts_v2_guide.md)** - Working with MCP tools and prompts
- **[file-handling.md](dev-docs/file-handling.md)** - File storage and management
- **[custom-prompting.md](dev-docs/custom-prompting.md)** - Customizing AI prompts
- **[advanced-features.md](dev-docs/advanced-features.md)** - Advanced functionality

### Technical Reference
- **[messages_types_to_ui.md](dev-docs/messages_types_to_ui.md)** - WebSocket message types and UI communication

## Archive (docs/archive/)

Historical planning documents, feature proposals, and design decisions. These may be outdated.

### AI Agent Notes
- **[GEMINI.md](archive/GEMINI.md)** - Gemini AI agent guidance
- **[mcp_note.md](archive/mcp_note.md)** - MCP integration notes
- **[old_v1_mcp_note.md](archive/old_v1_mcp_note.md)** - Historical MCP v1 notes
- **[v2_mcp_note.md](archive/v2_mcp_note.md)** - MCP v2 transition notes
- **[mcp_progress_note.md](archive/mcp_progress_note.md)** - MCP implementation progress
- **[mpc_progress_note.md](archive/mpc_progress_note.md)** - MPC progress notes

### Feature Plans
- **[RAG_update.md](archive/RAG_update.md)** - RAG feature updates
- **[agent_update.md](archive/agent_update.md)** - Agent feature updates
- **[agent_update_plan.md](archive/agent_update_plan.md)** - Agent implementation planning
- **[compliance-level-feature.md](archive/compliance-level-feature.md)** - Compliance level feature design
- **[file_library_implementation.md](archive/file_library_implementation.md)** - File library notes
- **[rag_compliance_filtering_plan.md](archive/rag_compliance_filtering_plan.md)** - RAG compliance filtering

### Planning & Ideas
- **[todo.md](archive/todo.md)** - Future enhancements and ideas
- **[app_name.md](archive/app_name.md)** - Application naming
- **[issue-10-workflow-proposal.md](archive/issue-10-workflow-proposal.md)** - Workflow proposal

## Planning (docs/planning/)

Active refactoring and feature planning documents.

- **[REFACTOR_PLAN_A.md](planning/REFACTOR_PLAN_A.md)** - Refactoring plan A
- **[REFACTOR_PLAN_B.md](planning/REFACTOR_PLAN_B.md)** - Refactoring plan B
- **[REFACTOR_PLAN_C.md](planning/REFACTOR_PLAN_C.md)** - Refactoring plan C
- **[endpoint_summary.md](planning/endpoint_summary.md)** - API endpoint summary
- **[frontend_endpoints.md](planning/frontend_endpoints.md)** - Frontend API endpoints
- **[issue-9-mcp-function-approvals-proposal.md](planning/issue-9-mcp-function-approvals-proposal.md)** - MCP approval proposal

## Additional Documentation

### Testing
- **[test/README.md](../test/README.md)** - Testing documentation
- **[test/CONTAINERIZED_TESTING.md](../test/CONTAINERIZED_TESTING.md)** - Containerized testing
- **[test_e2e/README.md](../test_e2e/README.md)** - End-to-end testing

### Component Documentation
- **[frontend/src/test/README.md](../frontend/src/test/README.md)** - Frontend test documentation
- **[mocks/mcp-http-mock/README.md](../mocks/mcp-http-mock/README.md)** - MCP HTTP mock
- **[mocks/s3-mock/README.md](../mocks/s3-mock/README.md)** - S3 mock documentation

### Root Level
- **[README.md](../README.md)** - Main project README
- **[SECURITY.md](../SECURITY.md)** - Security information
- **[.github/copilot-instructions.md](../.github/copilot-instructions.md)** - AI agent compact guide

## How to Find What You Need

| I want to... | Look here |
|--------------|-----------|
| Get the app running quickly | [docs/user-docs/quick-start.md](user-docs/quick-start.md) |
| Understand what the app does | [docs/user-docs/overview.md](user-docs/overview.md) |
| Set up a development environment | [docs/dev-docs/developer-setup.md](dev-docs/developer-setup.md) |
| Understand the architecture | [docs/dev-docs/CLAUDE.md](dev-docs/CLAUDE.md) |
| Learn about the backend | [docs/dev-docs/backend.md](dev-docs/backend.md) |
| Learn about the frontend | [docs/dev-docs/frontend.md](dev-docs/frontend.md) |
| Configure the application | [docs/dev-docs/configuration.md](dev-docs/configuration.md) |
| Create an MCP server | [docs/dev-docs/mcp-development.md](dev-docs/mcp-development.md) |
| Understand security | [docs/dev-docs/security_architecture.md](dev-docs/security_architecture.md) |
| See historical design decisions | [docs/archive/](archive/) |
| Run tests | [test/README.md](../test/README.md) |

## Maintenance Notes

This documentation structure was organized on 2025-01-04 to improve onboarding by clearly separating:
1. User documentation - for getting started and using the app
2. Developer documentation - for understanding how things work
3. Planning/archive - for historical context and design decisions

For the most current information, always check:
- [docs/dev-docs/CLAUDE.md](dev-docs/CLAUDE.md) - Kept most up-to-date
- [.github/copilot-instructions.md](../.github/copilot-instructions.md) - Compact reference
