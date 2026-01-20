# Compliance and Data Security

Last updated: 2026-01-19

The compliance system is designed to prevent the unintentional mixing of data from different security environments. This is essential for organizations that handle sensitive information.

## Compliance Levels

You can assign a `compliance_level` to LLM endpoints, RAG data sources, and MCP servers. These levels are defined in `config/defaults/compliance-levels.json` (which can be overridden).

**Example:** A tool that accesses internal-only data can be marked with `compliance_level: "Internal"`, while a tool that uses a public API can be marked as `compliance_level: "Public"`.

## The Allowlist Model

The compliance system uses an explicit **allowlist**. Each compliance level defines which other levels it is allowed to interact with. This prevents data from a highly secure environment (e.g., "HIPAA") from being accidentally sent to a less secure one (e.g., "Public").

For example, a session running with a "HIPAA" compliance level will not be able to use tools or data sources marked as "Public", preventing sensitive data from being exposed.
