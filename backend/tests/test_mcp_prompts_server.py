import pytest

from infrastructure.app_factory import app_factory


@pytest.mark.asyncio
async def test_mcp_prompts_discovery_includes_expert_dog_trainer():
    mcp = app_factory.get_mcp_manager()

    # Ensure fresh clients and prompt discovery
    await mcp.initialize_clients()
    await mcp.discover_prompts()

    # The prompts server should be configured (from config/overrides mcp.json)
    assert "prompts" in mcp.available_prompts, "prompts server not discovered"

    server_data = mcp.available_prompts["prompts"]
    prompts = server_data.get("prompts", [])
    names = {getattr(p, "name", None) for p in prompts}

    assert "expert_dog_trainer" in names, f"expert_dog_trainer not in discovered prompts: {names}"
