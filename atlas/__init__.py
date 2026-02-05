"""
Atlas - Full-stack LLM chat interface with MCP integration.

This package provides both a Python API for programmatic access and
CLI tools for interacting with LLMs.

Example usage:
    from atlas import AtlasClient, ChatResult

    client = AtlasClient()
    result = await client.chat("Hello, world!")
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


def __getattr__(name: str):
    """Lazy import to avoid loading heavy dependencies at module import time."""
    if name == "AtlasClient":
        from atlas.atlas_client import AtlasClient
        globals()["AtlasClient"] = AtlasClient  # Cache for subsequent accesses
        return AtlasClient
    if name == "ChatResult":
        from atlas.atlas_client import ChatResult
        globals()["ChatResult"] = ChatResult  # Cache for subsequent accesses
        return ChatResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
