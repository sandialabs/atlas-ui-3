"""
Non-interactive CLI for Atlas chat.

Usage:
    python atlas_chat_cli.py "Summarize the latest docs" --model gpt-4o
    python atlas_chat_cli.py "Use the search tool" --tools server_tool1
    python atlas_chat_cli.py --list-tools
    echo "prompt" | python atlas_chat_cli.py - --model gpt-4o
    python atlas_chat_cli.py "prompt" --env-file /path/to/custom.env
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Suppress LiteLLM verbose stdout noise BEFORE any transitive import of litellm.
# litellm._logging reads LITELLM_LOG at import time and defaults to DEBUG.
if "LITELLM_LOG" not in os.environ:
    os.environ["LITELLM_LOG"] = "ERROR"

from dotenv import load_dotenv


# Phase 1: Parse --env-file early, before loading env and importing atlas code.
# This allows specifying a custom .env file that affects all subsequent imports.
def _get_env_file_from_args() -> tuple[Path, bool]:
    """Extract --env-file from sys.argv without full parsing.

    Returns:
        Tuple of (env_path, is_custom) where is_custom is True if user
        explicitly provided --env-file, False for default .env path.
    """
    default_env = Path(__file__).resolve().parents[1] / ".env"
    for i, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--env-file" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]), True
        if arg.startswith("--env-file="):
            return Path(arg.split("=", 1)[1]), True
    return default_env, False


def _extract_flag_value(argv: list[str], flag_name: str) -> str | None:
    """Extract a flag value from argv.

    Supports both `--flag value` and `--flag=value` forms.
    """
    for i, arg in enumerate(argv):
        if arg == flag_name and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(flag_name + "="):
            return arg.split("=", 1)[1]
    return None


def _apply_config_overrides_from_args() -> None:
    """Apply config path overrides from CLI args as env vars.

    This must run BEFORE load_dotenv() and BEFORE importing atlas code, so
    flags override values coming from .env files.
    """
    argv = sys.argv[1:]

    # Directories
    overrides_dir = _extract_flag_value(argv, "--config-overrides")
    defaults_dir = _extract_flag_value(argv, "--config-defaults")
    if overrides_dir:
        os.environ["APP_CONFIG_OVERRIDES"] = str(Path(overrides_dir).expanduser().resolve())
    if defaults_dir:
        os.environ["APP_CONFIG_DEFAULTS"] = str(Path(defaults_dir).expanduser().resolve())

    def _apply_config_file_override(flag: str, env_var: str) -> None:
        value = _extract_flag_value(argv, flag)
        if not value:
            return
        p = Path(value).expanduser()
        # If user provides a path, set *_CONFIG_FILE to basename and (unless
        # explicitly set) point APP_CONFIG_OVERRIDES at the containing directory.
        if "/" in value or p.parent != Path("."):
            resolved = p.resolve()
            os.environ[env_var] = resolved.name
            if "APP_CONFIG_OVERRIDES" not in os.environ:
                os.environ["APP_CONFIG_OVERRIDES"] = str(resolved.parent)
        else:
            os.environ[env_var] = value

    # Individual config files
    _apply_config_file_override("--mcp-config", "MCP_CONFIG_FILE")
    _apply_config_file_override("--rag-sources-config", "RAG_SOURCES_CONFIG_FILE")
    _apply_config_file_override("--llm-config", "LLM_CONFIG_FILE")
    _apply_config_file_override("--help-config", "HELP_CONFIG_FILE")
    _apply_config_file_override("--messages-config", "MESSAGES_CONFIG_FILE")
    _apply_config_file_override("--tool-approvals-config", "TOOL_APPROVALS_CONFIG_FILE")
    _apply_config_file_override("--splash-config", "SPLASH_CONFIG_FILE")
    _apply_config_file_override("--file-extractors-config", "FILE_EXTRACTORS_CONFIG_FILE")

_env_file_path, _env_file_is_custom = _get_env_file_from_args()
if not _env_file_path.exists():
    if _env_file_is_custom:
        print(f"Error: specified env file not found: {_env_file_path}", file=sys.stderr)
        sys.exit(2)
    else:
        print(f"Warning: default env file not found: {_env_file_path}", file=sys.stderr)

# Phase 1b: Apply config override flags before loading the env file.
_apply_config_overrides_from_args()
load_dotenv(dotenv_path=str(_env_file_path))

# Now safe to import atlas code (which transitively imports litellm)
from atlas.atlas_client import AtlasClient  # noqa: E402

# Belt-and-suspenders: also quiet the Python loggers litellm creates
for _name in ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router", "litellm", "httpx"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-chat",
        description="Non-interactive CLI for Atlas LLM chat with MCP tools and RAG.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Chat prompt text, or '-' to read from stdin.",
    )
    parser.add_argument("--model", default=None, help="LLM model name (uses config default if omitted).")
    parser.add_argument("--tools", default=None, help="Comma-separated list of tool names to enable.")
    parser.add_argument("-o", "--output", default=None, help="Write final response to file path.")
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output structured JSON.")
    parser.add_argument("--user-email", default=None, help="Override user identity.")
    parser.add_argument("--list-tools", action="store_true", help="Print available tools and exit.")
    parser.add_argument(
        "--data-sources",
        default=None,
        help="Comma-separated list of RAG data source names to query.",
    )
    parser.add_argument(
        "--only-rag",
        action="store_true",
        help="Use only RAG without tools (RAG-only mode).",
    )
    parser.add_argument(
        "--list-data-sources",
        action="store_true",
        help="Print available RAG data sources and exit.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to custom .env file (default: project root .env). Parsed early before other imports.",
    )

    # Config override flags (useful for testing and CI)
    parser.add_argument(
        "--config-overrides",
        default=None,
        help="Override config overrides directory (sets APP_CONFIG_OVERRIDES).",
    )
    parser.add_argument(
        "--config-defaults",
        default=None,
        help="Override config defaults directory (sets APP_CONFIG_DEFAULTS).",
    )
    parser.add_argument(
        "--llm-config",
        default=None,
        help="Override LLM config file (sets LLM_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--mcp-config",
        default=None,
        help="Override MCP config file (sets MCP_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--rag-sources-config",
        default=None,
        help="Override RAG sources config file (sets RAG_SOURCES_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--help-config",
        default=None,
        help="Override help config file (sets HELP_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--messages-config",
        default=None,
        help="Override messages config file (sets MESSAGES_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--tool-approvals-config",
        default=None,
        help="Override tool approvals config file (sets TOOL_APPROVALS_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--splash-config",
        default=None,
        help="Override splash config file (sets SPLASH_CONFIG_FILE). Accepts a filename or path.",
    )
    parser.add_argument(
        "--file-extractors-config",
        default=None,
        help="Override file extractors config file (sets FILE_EXTRACTORS_CONFIG_FILE). Accepts a filename or path.",
    )
    return parser


async def list_tools(*, json_output: bool = False) -> int:
    """Discover and print all available tools in CLI-usable format."""
    client = AtlasClient()
    try:
        await client.initialize()
        mcp_manager = client._factory.get_mcp_manager()
        tool_index = getattr(mcp_manager, "_tool_index", {})
        if not tool_index:
            if json_output:
                print(json.dumps({"servers": {}, "tools": []}, indent=2))
                return 0
            print("No tools discovered.", file=sys.stderr)
            return 1
        # Group by server
        servers: dict[str, list[str]] = {}
        for full_name, info in sorted(tool_index.items()):
            server = info["server"]
            servers.setdefault(server, []).append(full_name)
        if json_output:
            tools = [name for names in servers.values() for name in names]
            print(json.dumps({"servers": servers, "tools": tools}, indent=2))
            return 0
        for server, tools in servers.items():
            print(f"{server}:")
            for name in tools:
                print(f"  {name}")
        return 0
    finally:
        await client.cleanup()


async def list_data_sources(user_email: str = None, *, json_output: bool = False) -> int:
    """Discover and print all available RAG data sources."""
    client = AtlasClient()
    try:
        result = await client.list_data_sources(user_email=user_email)
        if json_output:
            print(json.dumps(result, indent=2))
            return 0
        servers = result.get("servers", {})
        discovered = result.get("sources", [])

        if not servers and not discovered:
            print("No RAG data sources configured.", file=sys.stderr)
            return 1

        # Show configured servers
        if servers:
            print("Configured RAG servers:")
            for name, info in sorted(servers.items()):
                display_name = info.get("display_name", name)
                source_type = info.get("type", "unknown")
                desc = info.get("description", "")
                print(f"  {display_name} ({source_type})")
                if desc:
                    print(f"    {desc}")
            print()

        # Show discovered sources (these are the actual --data-sources values)
        if discovered:
            print("Available data sources (use with --data-sources):")
            for source_id in discovered:
                print(f"  {source_id}")
        else:
            print("No data sources discovered. Servers may not expose rag_discover_resources.")
            print("For MCP RAG servers, try: --data-sources SERVER_NAME:SOURCE_ID")

        return 0
    finally:
        await client.cleanup()


async def run(args: argparse.Namespace) -> int:
    if args.list_tools:
        return await list_tools(json_output=args.json_output)

    if args.list_data_sources:
        return await list_data_sources(user_email=args.user_email, json_output=args.json_output)

    # Resolve prompt
    prompt = args.prompt
    if prompt == "-" or (prompt is None and not sys.stdin.isatty()):
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("Error: no prompt provided.", file=sys.stderr)
        return 2

    selected_tools = None
    if args.tools:
        selected_tools = [t.strip() for t in args.tools.split(",") if t.strip()]

    selected_data_sources = None
    if args.data_sources:
        selected_data_sources = [s.strip() for s in args.data_sources.split(",") if s.strip()]

    # In JSON or output-file mode, collect rather than stream
    streaming = not args.json_output and args.output is None

    client = AtlasClient()
    try:
        result = await client.chat(
            prompt=prompt,
            model=args.model,
            agent_mode=False,
            selected_tools=selected_tools,
            selected_data_sources=selected_data_sources,
            only_rag=args.only_rag,
            user_email=args.user_email,
            session_id=None,
            streaming=streaming,
        )

        if args.json_output:
            print(json.dumps(result.to_dict(), indent=2))
        elif args.output:
            Path(args.output).write_text(result.message, encoding="utf-8")
            print(f"Output written to {args.output}", file=sys.stderr)
        # If streaming, output was already printed live

        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        logging.getLogger(__name__).debug("CLI error details", exc_info=True)
        return 1
    finally:
        await client.cleanup()


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
