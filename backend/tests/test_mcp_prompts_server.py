import json
from pathlib import Path

import pytest

from modules.mcp_tools.client import MCPToolManager


@pytest.mark.asyncio
async def test_mcp_prompts_discovery_includes_expert_dog_trainer():
    # Use the example prompts MCP config file so this test
    # exercises the real JSON configuration used for prompts.
    # tests run with cwd=backend/, so resolve from backend root
    backend_root = Path(__file__).parent.parent
    print(backend_root)
    project_root = backend_root.parent
    print(project_root)
    config_path = project_root / "config" / "mcp-example-configs" / "mcp-prompts.json"
    print(config_path)
    assert config_path.exists(), f"Missing example prompts config: {config_path}"

    # Sanity-check that the JSON contains a "prompts" server.
    data = json.loads(config_path.read_text())
    assert "prompts" in data, "prompts server not defined in example config"

    mcp = MCPToolManager(config_path=str(config_path))

    # Ensure fresh clients and prompt discovery
    await mcp.initialize_clients()
    await mcp.discover_prompts()

    # The prompts server should be configured (from defaults or overrides).
    assert "prompts" in mcp.available_prompts, "prompts server not discovered"

    server_data = mcp.available_prompts["prompts"]
    prompts = server_data.get("prompts", [])
    names = {getattr(p, "name", None) for p in prompts}

    assert "expert_dog_trainer" in names, f"expert_dog_trainer not in discovered prompts: {names}"
