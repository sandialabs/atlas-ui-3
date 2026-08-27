"""Guard the shipped MCP server defaults in atlas/config/mcp.json.

The packaged ``atlas/config/mcp.json`` is the source of truth for the MCP
servers that ship with ATLAS. Nothing else in CI parses this file, so a
malformed edit (a bad server entry, a dropped field, an unpinned third-party
command) would degrade every MCP server at startup while CI stayed green.

These tests pin the entries that carry policy or supply-chain weight so a
regression is caught here instead of in production.
"""

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGED_MCP_JSON = _REPO_ROOT / "atlas" / "config" / "mcp.json"


@pytest.fixture(scope="module")
def packaged_mcp_config() -> dict:
    """Load the packaged MCP server config that ships with the wheel."""
    with _PACKAGED_MCP_JSON.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_packaged_mcp_json_is_a_dict_of_servers(packaged_mcp_config: dict) -> None:
    assert isinstance(packaged_mcp_config, dict)
    assert len(packaged_mcp_config) > 0, "packaged mcp.json has no servers"


def test_perplexity_entry_shape(packaged_mcp_config: dict) -> None:
    """The bundled Perplexity server must keep its opt-in/pinned shape.

    A third-party server that forwards query text to an external API ships
    DISABLED, pinned to a known-good version, and scoped to ``users``. If any
    of these drift, this test fails before the change lands.
    """
    assert "perplexity" in packaged_mcp_config, "perplexity server missing from packaged defaults"
    entry = packaged_mcp_config["perplexity"]

    # Disabled by default: enabling forwards queries to Perplexity (data egress),
    # so it must be a deliberate per-deployment opt-in, not a default-on behavior.
    assert entry.get("enabled") is False, "perplexity must ship disabled (enabled: false)"

    # Pinned, supply-chain-hardened launch command.
    assert entry["command"] == [
        "npx", "-yq", "--ignore-scripts", "@perplexity-ai/mcp-server@1.2.0"
    ], "perplexity command must be the pinned, --ignore-scripts npx invocation"

    assert entry["transport"] == "stdio"
    assert entry["groups"] == ["users"]
    assert entry["compliance_level"] == "Public"

    # API key must come from the environment, never hardcoded.
    env = entry.get("env", {})
    assert env.get("PERPLEXITY_API_KEY") == "${PERPLEXITY_API_KEY}", (
        "PERPLEXITY_API_KEY must use ${...} substitution, not a literal"
    )


def test_perplexity_entry_loads_through_pydantic_model() -> None:
    """The packaged entry must round-trip through the MCPConfig model so a
    field the model rejects is caught here, not at runtime."""
    from atlas.modules.config.models import MCPConfig

    with _PACKAGED_MCP_JSON.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    cfg = MCPConfig(servers={"perplexity": raw["perplexity"]})
    server = cfg.servers["perplexity"]
    assert server.enabled is False
    assert server.transport == "stdio"
    assert server.command == ["npx", "-yq", "--ignore-scripts", "@perplexity-ai/mcp-server@1.2.0"]
