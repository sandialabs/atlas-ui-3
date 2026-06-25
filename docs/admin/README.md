# Administrator's Guide

Last updated: 2026-06-22

For administrators responsible for deploying, configuring, and managing Atlas UI 3.

## Configuration

- [Configuration Architecture](configuration.md) - Understanding the layered configuration system
- [MCP Server Configuration](mcp-servers.md) - Setting up and configuring MCP tool servers
- [LLM Configuration](llm-config.md) - Configuring Large Language Models
- [RAG Configuration](external-rag-api.md) - Configuring RAG providers (mock, ATLAS API, MCP)
- [Model Capabilities Enforcement](model-capabilities-2026-04-02.md) - Declaring and enforcing per-model tool/vision support

## Security & Access Control

- [Authentication & Authorization](authentication.md) - User authentication and group-based access control
- [MCP API Key Authentication](mcp-server-authentication.md) - Authenticating to protected MCP servers
- [MCP Wormhole Authentication](mcp-wormhole-authentication.md) - Forwarding per-session Wormhole subtokens to MCP servers
- [WebSocket Auth Testing](websocket-auth-testing.md) - Testing WebSocket authentication with wscat
- [Compliance & Data Security](compliance.md) - Compliance levels and data segregation
- [Tool Approval System](tool-approval.md) - Managing tool execution permissions
- [Email Domain Whitelist](domain-whitelist.md) - Restricting access by email domain
- [Globus OAuth Integration](globus-auth-integration-2026-02-24.md) - OAuth for ALCF endpoints

## Storage & Infrastructure

- [File Storage (S3)](file-storage.md) - Configuring S3-compatible object storage
- [Troubleshooting File Access](troubleshooting-file-access.md) - Common file-access issues for MCP servers

## Operations

- [Logging & Monitoring](logging-monitoring.md) - Application logs and health monitoring
- [Admin Panel](admin-panel.md) - Using the administrative interface
- [User Feedback](feedback.md) - Collecting and reviewing user feedback
- [Chat History Persistence](chat-history.md) - Conversation storage and retention
- [PyPI Release Guide](pypi-releases.md) - Publishing the `atlas-chat` package

## UI Customization

- [Help Page](help-config.md) - Customizing the Help/About content
- [Splash Screen](splash-config.md) - Configuring the startup splash screen
