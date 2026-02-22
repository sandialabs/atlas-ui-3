"""
Atlas - Full-stack LLM chat interface with MCP integration.

This package provides both a Python API for programmatic access and
CLI tools for interacting with LLMs.

Example usage (async):
    import asyncio
    from atlas import AtlasClient

    async def main():
        client = AtlasClient()
        result = await client.chat("Hello, world!")
        print(result.message)

    asyncio.run(main())

Synchronous usage:
    from atlas import AtlasClient

    client = AtlasClient()
    result = client.chat_sync("Hello, world!")
    print(result.message)

CLI tools (after pip install):
    atlas-chat "Your prompt here" --model gpt-4o
    atlas-server --port 8000
"""

from atlas.version import VERSION

__version__ = VERSION
__all__ = [
    "AtlasClient",
    "ChatResult",
    "VERSION",
    "__version__",
]


def __getattr__(name):
    if name in ("AtlasClient", "ChatResult"):
        from atlas.atlas_client import AtlasClient, ChatResult

        globals()["AtlasClient"] = AtlasClient
        globals()["ChatResult"] = ChatResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
