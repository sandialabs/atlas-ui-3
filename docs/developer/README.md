# Developer's Guide

Last updated: 2026-06-22

Technical reference for contributors to Atlas UI 3. These pages describe how
the system works *today*. Point-in-time records of how individual features were
built live under [design-notes/](design-notes/README.md).

## Architecture & Conventions

- [Architecture Overview](architecture.md) - Backend clean-architecture layers and frontend context model
- [Development Conventions](conventions.md) - Coding standards and project conventions

## MCP Tool Development

- [MCP Tool Outputs](mcp-tool-outputs.md) - How tool results are rendered (canvas, files, text)
- [MCP Tool File I/O Guide](mcp-file-io.md) - Reading and writing files from MCP tools
- [MCP Server Logging](mcp-server-logging.md) - Emitting and surfacing MCP server logs
- [LLM Sampling in MCP Tools](sampling.md) - Letting tools call back into the LLM
- [Interactive Tool Elicitation](elicitation.md) - Tools that prompt the user mid-execution
- [Progress Updates and Intermediate Results](progress-updates.md) - Streaming progress from long-running tools
- [Adding Custom Canvas Renderers](canvas-renderers.md) - Extending the canvas with new viewers

## Files & Storage

- [Working with Files (S3 Storage)](working-with-files.md) - File storage model and remote MCP access
- [File Content Extraction](file-content-extraction.md) - Extracting text from uploaded documents

## Error Handling

- [Error Handling Improvements](error-handling-improvements.md) - LLM error classification and surfacing
- [Error Flow Diagram](error-flow-diagram.md) - End-to-end error flow diagram
- [File Upload Issue Fix](file-upload-fix-summary.md) - Technical summary of the file-upload fix

## Releases & Documentation

- [Release Process](release-process.md) - Monthly release cadence, versioning, hotfix flow, rollback
- [Documentation Bundling](documentation-bundling.md) - Automated documentation bundle for CI/CD and AI agents

## Design Notes

- [Feature design notes](design-notes/README.md) - Dated records of how specific shipped features were built
