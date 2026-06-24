# Design Notes

Last updated: 2026-06-22

Point-in-time records of how and why specific features were built. Each is
dated and reflects the system as of that date — useful background for
understanding current behavior, but not maintained as living reference. For
docs that describe how the system works today, see the
[Developer's Guide](../README.md).

## Notes

- [Agentic Loop](agentic-loop-2026-02-23.md) - The single native agent loop (PR #664)
- [Multi-Tool Calling](multi-tool-calling-2026-02-22.md) - Parallel execution of tool calls
- [LLM Token Streaming](llm-token-streaming-2026-02-22.md) - Token streaming architecture
- [Follow-up Question Suggestions](follow-up-suggestions-2026-03-18.md) - Suggested follow-up prompts
- [Config Loading Strategy](config-loading-strategy-2026-03-12.md) - localStorage cache + split config endpoint
- [3-State Chat Save Mode](chat-save-modes-2026-02-25.md) - Incognito / local / server save modes
- [MCP Session Isolation](mcp-session-isolation-2026-03-15.md) - Per-user MCP session isolation
- [_mcp_data Injection for MCP Tools](mcp-data-injection-2026-02-03.md) - Injecting request context into tools
- [Vision Image Support](vision-image-support-2026-03-23.md) - Image input handling and limits
- [Native PDF Document Support](pdf-document-support-2026-06-12.md) - Native PDF content blocks
- [PPTX Generation MCP Server](pptx-generation-2026-02-09.md) - PowerPoint generation tool
- [Code Executor v2](code-executor-v2-2026-04-30.md) - Stateful sandboxed Python execution MCP
- [Atlas CLI and Python API](cli-usage-2026-01-27.md) - CLI/Python client design and usage
- [Metrics Logging](metrics-logging.md) - Metrics logging feature
- [Animated Logo](animated-logo-2026-02-27.md) - Animated logo feature flag and effects
- [Theme Default and Warning Contrast](theme-default-and-warning-contrast-2026-06-14.md) - Dark default + light-mode warning contrast (PR #654)
