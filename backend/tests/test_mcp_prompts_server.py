import pytest
from unittest.mock import patch, Mock

from modules.mcp_tools.client import MCPToolManager


@pytest.mark.asyncio
async def test_mcp_prompts_discovery_includes_expert_dog_trainer():
    # Use the example prompts MCP config to ensure the prompts
    # server is available in tests regardless of app settings
    # and avoid depending on the global config manager.
    with patch("modules.mcp_tools.client.config_manager") as mock_cm:
        mock_cm.mcp_config.servers = {
            "prompts": Mock(name="prompts_server_config")
        }
        mock_cm.mcp_config.servers["prompts"].model_dump.return_value = {
            "command": ["python", "mcp/prompts/main.py"],
            "cwd": "backend",
            "groups": ["users"],
        }

        mcp = MCPToolManager()

    # Ensure fresh clients and prompt discovery
    await mcp.initialize_clients()
    await mcp.discover_prompts()

    # The prompts server should be configured (from config/overrides mcp.json)
    assert "prompts" in mcp.available_prompts, "prompts server not discovered"

    server_data = mcp.available_prompts["prompts"]
    prompts = server_data.get("prompts", [])
    names = {getattr(p, "name", None) for p in prompts}

    assert "expert_dog_trainer" in names, f"expert_dog_trainer not in discovered prompts: {names}"
