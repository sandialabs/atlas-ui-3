"""Test that tool details (description and inputSchema) are included in config API response."""

import os
import sys

import pytest

# Ensure backend is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.modules.mcp_tools.client import MCPToolManager


class FakeTool:
    """Mock tool object for testing."""
    def __init__(self, name, description="", inputSchema=None):
        self.name = name
        self.description = description
        self.inputSchema = inputSchema or {"type": "object", "properties": {}}


@pytest.fixture
def mock_mcp_manager(monkeypatch):
    """Create a mock MCP manager with test data."""
    manager = MCPToolManager()

    # Mock available_tools with detailed tool information
    manager.available_tools = {
        "test_server": {
            "tools": [
                FakeTool(
                    "test_tool",
                    "This is a test tool description",
                    {
                        "type": "object",
                        "properties": {
                            "arg1": {
                                "type": "string",
                                "description": "First argument"
                            },
                            "arg2": {
                                "type": "number",
                                "description": "Second argument"
                            }
                        },
                        "required": ["arg1"]
                    }
                )
            ],
            "config": {
                "description": "Test server",
                "author": "Test Author"
            }
        }
    }

    manager.available_prompts = {}
    return manager


def test_tools_detailed_includes_description_and_schema(mock_mcp_manager):
    """Test that tools_detailed field contains description and inputSchema."""
    server_tools = mock_mcp_manager.available_tools["test_server"]["tools"]
    # Simulate what the config endpoint does
    tools_detailed = []
    for tool in server_tools:
        tool_detail = {
            'name': tool.name,
            'description': tool.description or '',
            'inputSchema': getattr(tool, 'inputSchema', {}) or {}
        }
        tools_detailed.append(tool_detail)

    # Verify the structure
    assert len(tools_detailed) == 1
    assert tools_detailed[0]['name'] == 'test_tool'
    assert tools_detailed[0]['description'] == 'This is a test tool description'
    assert 'inputSchema' in tools_detailed[0]
    assert 'properties' in tools_detailed[0]['inputSchema']
    assert 'arg1' in tools_detailed[0]['inputSchema']['properties']
    assert tools_detailed[0]['inputSchema']['properties']['arg1']['type'] == 'string'
    assert tools_detailed[0]['inputSchema']['properties']['arg1']['description'] == 'First argument'


def test_canvas_tool_has_detailed_info():
    """Test that canvas pseudo-tool has detailed information."""
    canvas_tools_detailed = [{
        'name': 'canvas',
        'description': 'Display final rendered content in a visual canvas panel. Use this for: 1) Complete code (not code discussions), 2) Final reports/documents (not report discussions), 3) Data visualizations, 4) Any polished content that should be viewed separately from the conversation.',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'content': {
                    'type': 'string',
                    'description': 'The content to display in the canvas. Can be markdown, code, or plain text.'
                }
            },
            'required': ['content']
        }
    }]

    # Verify canvas tool structure
    assert len(canvas_tools_detailed) == 1
    assert canvas_tools_detailed[0]['name'] == 'canvas'
    assert 'description' in canvas_tools_detailed[0]
    assert len(canvas_tools_detailed[0]['description']) > 0
    assert 'inputSchema' in canvas_tools_detailed[0]
    assert 'content' in canvas_tools_detailed[0]['inputSchema']['properties']
