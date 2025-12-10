# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #TBD - 2024-12-10
- Add OAuth 2.1 authentication support for MCP servers using FastMCP OAuth helper
- Add secure JWT storage with Fernet encryption for user-uploaded tokens
- Add admin API endpoints for JWT management (upload, check, delete, list)
- Add oauth_config field to MCPServerConfig for OAuth settings (scopes, client name, callback port, token storage)
- Update MCP client initialization to support OAuth flow with automatic token refresh
- Add encrypted token storage using key-value library with Fernet encryption
- Update authentication priority: OAuth > Manual JWT > Bearer Token > None
- Add comprehensive OAuth documentation in docs/admin/mcp-oauth.md
- Update requirements.txt to include FastMCP 2.6.0+ and key-value library

### PR #156 - 2024-12-07
- Add CHANGELOG.md to track changes across PRs
- Update agent instructions to require changelog entries for each PR

## Recent Changes

### PR #157 - 2024-12-07
- Enhanced ToolsPanel UI with improved visual separation between tools and prompts
- Added section headers with icons for tools and prompts
- Updated color scheme to use consistent green styling for both tools and prompts
- Added horizontal divider between tools and prompts sections
- Increased font size and weight for section headers
- Improved vertical spacing between UI sections

### PR #155 - 2024-12-06
- Add automated documentation bundling for CI/CD artifacts
