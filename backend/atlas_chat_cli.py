"""
Non-interactive CLI for Atlas chat.

Usage:
    python atlas_chat_cli.py "Summarize the latest docs" --model gpt-4o
    python atlas_chat_cli.py "Use the search tool" --tools server_tool1
    python atlas_chat_cli.py --list-tools
    echo "prompt" | python atlas_chat_cli.py - --model gpt-4o
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

# Load env (won't overwrite LITELLM_LOG we just set)
load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / ".env"))

# Now safe to import atlas code (which transitively imports litellm)
from atlas_client import AtlasClient  # noqa: E402

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
    parser.add_argument("--json", dest="json_output", action="store_true", help="Output structured JSON.")
    parser.add_argument("--user-email", default=None, help="Override user identity.")
    parser.add_argument("--list-tools", action="store_true", help="Print available tools and exit.")
    return parser


async def list_tools() -> int:
    """Discover and print all available tools in CLI-usable format."""
    client = AtlasClient()
    try:
        await client.initialize()
        mcp_manager = client._factory.get_mcp_manager()
        tool_index = getattr(mcp_manager, "_tool_index", {})
        if not tool_index:
            print("No tools discovered.", file=sys.stderr)
            return 1
        # Group by server
        servers: dict[str, list[str]] = {}
        for full_name, info in sorted(tool_index.items()):
            server = info["server"]
            servers.setdefault(server, []).append(full_name)
        for server, tools in servers.items():
            print(f"{server}:")
            for name in tools:
                print(f"  {name}")
        return 0
    finally:
        await client.cleanup()


async def run(args: argparse.Namespace) -> int:
    if args.list_tools:
        return await list_tools()

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

    # In JSON mode, collect rather than stream
    streaming = not args.json_output

    client = AtlasClient()
    try:
        result = await client.chat(
            prompt=prompt,
            model=args.model,
            agent_mode=False,
            selected_tools=selected_tools,
            user_email=args.user_email,
            session_id=None,
            streaming=streaming,
        )

        if args.json_output:
            print(json.dumps(result.to_dict(), indent=2))
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
