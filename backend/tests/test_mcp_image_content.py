"""Tests for ImageContent handling in MCP tool results.

These tests verify that Atlas can extract and process ImageContent items
from MCP tool responses and convert them to artifacts for display.
"""

import pytest
from unittest.mock import AsyncMock, patch

from backend.modules.mcp_tools.client import MCPToolManager
from domain.messages.models import ToolCall


class MockImageContent:
    """Mock for MCP ImageContent item."""
    def __init__(self, data: str, mime_type: str = "image/png"):
        self.type = "image"
        self.data = data
        self.mimeType = mime_type


class MockTextContent:
    """Mock for MCP text content item."""
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class MockMCPResultWithImage:
    """Mock MCP result that includes ImageContent in content array."""
    def __init__(self, image_data: str, mime_type: str = "image/png"):
        self.content = [MockImageContent(image_data, mime_type)]
        self.structured_content = None
        self.data = None
        self.is_error = False


class MockMCPResultWithMultipleImages:
    """Mock MCP result with multiple ImageContent items."""
    def __init__(self, images: list):
        self.content = [MockImageContent(img["data"], img["mime"]) for img in images]
        self.structured_content = None
        self.data = None
        self.is_error = False


class MockMCPResultWithMixedContent:
    """Mock MCP result with both TextContent and ImageContent."""
    def __init__(self, text: str, image_data: str):
        self.content = [
            MockTextContent(text),
            MockImageContent(image_data)
        ]
        self.structured_content = None
        self.data = None
        self.is_error = False


class TestImageContentHandling:
    """Tests for extracting ImageContent from MCP tool results."""

    @pytest.mark.asyncio
    async def test_extract_single_image_content(self):
        """Test extraction of a single ImageContent item."""
        manager = MCPToolManager.__new__(MCPToolManager)
        
        # Mock tool object
        class MockTool:
            def __init__(self, name):
                self.name = name
        
        # Create a tool call
        tool_call = ToolCall(
            id="test-call-1",
            name="generate_image",
            arguments={}
        )
        
        # Mock the call_tool to return ImageContent
        image_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg=="
        raw_result = MockMCPResultWithImage(image_b64, "image/png")
        
        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            
            # Mock _tool_index with correct structure
            manager._tool_index = {
                "generate_image": {
                    "server": "test-server",
                    "tool": MockTool("generate_image")
                }
            }
            
            result = await manager.execute_tool(tool_call, context={})
            
            # Verify artifacts were created
            assert result.artifacts is not None
            assert len(result.artifacts) == 1
            
            artifact = result.artifacts[0]
            assert artifact["name"] == "image_0.png"
            assert artifact["b64"] == image_b64
            assert artifact["mime"] == "image/png"
            assert artifact["viewer"] == "image"
            assert "generate_image" in artifact["description"]
            
            # Verify display config was auto-created
            assert result.display_config is not None
            assert result.display_config["primary_file"] == "image_0.png"
            assert result.display_config["open_canvas"] is True

    @pytest.mark.asyncio
    async def test_extract_multiple_image_contents(self):
        """Test extraction of multiple ImageContent items."""
        manager = MCPToolManager.__new__(MCPToolManager)
        
        # Mock tool object
        class MockTool:
            def __init__(self, name):
                self.name = name
        
        tool_call = ToolCall(
            id="test-call-2",
            name="generate_multiple",
            arguments={}
        )
        
        images = [
            {"data": "base64data1", "mime": "image/png"},
            {"data": "base64data2", "mime": "image/jpeg"},
            {"data": "base64data3", "mime": "image/gif"}
        ]
        raw_result = MockMCPResultWithMultipleImages(images)
        
        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "generate_multiple": {
                    "server": "test-server",
                    "tool": MockTool("generate_multiple")
                }
            }
            
            result = await manager.execute_tool(tool_call, context={})
            
            # Verify all images were extracted
            assert len(result.artifacts) == 3
            
            # Check each artifact
            for i, (artifact, img) in enumerate(zip(result.artifacts, images)):
                expected_ext = img["mime"].split("/")[-1]
                assert artifact["name"] == f"image_{i}.{expected_ext}"
                assert artifact["b64"] == img["data"]
                assert artifact["mime"] == img["mime"]
                assert artifact["viewer"] == "image"

    @pytest.mark.asyncio
    async def test_extract_mixed_content(self):
        """Test extraction when both TextContent and ImageContent are present."""
        manager = MCPToolManager.__new__(MCPToolManager)
        
        # Mock tool object
        class MockTool:
            def __init__(self, name):
                self.name = name
        
        tool_call = ToolCall(
            id="test-call-3",
            name="mixed_tool",
            arguments={}
        )
        
        text = "Here is your visualization"
        image_b64 = "base64imagedata"
        raw_result = MockMCPResultWithMixedContent(text, image_b64)
        
        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "mixed_tool": {
                    "server": "test-server",
                    "tool": MockTool("mixed_tool")
                }
            }
            
            result = await manager.execute_tool(tool_call, context={})
            
            # Verify image was extracted (uses image counter, so first image is image_0)
            assert len(result.artifacts) == 1
            artifact = result.artifacts[0]
            assert artifact["name"] == "image_0.png"
            assert artifact["b64"] == image_b64

    @pytest.mark.asyncio
    async def test_no_image_content(self):
        """Test that non-image content doesn't create artifacts."""
        manager = MCPToolManager.__new__(MCPToolManager)
        
        # Mock tool object
        class MockTool:
            def __init__(self, name):
                self.name = name
        
        tool_call = ToolCall(
            id="test-call-4",
            name="text_only",
            arguments={}
        )
        
        # Create a result with only text content
        class MockTextOnlyResult:
            def __init__(self):
                self.content = [MockTextContent("Just text")]
                self.structured_content = None
                self.data = None
                self.is_error = False
        
        raw_result = MockTextOnlyResult()
        
        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "text_only": {
                    "server": "test-server",
                    "tool": MockTool("text_only")
                }
            }
            
            result = await manager.execute_tool(tool_call, context={})
            
            # Verify no artifacts were created
            assert len(result.artifacts) == 0
            # Display config should not be auto-created
            assert result.display_config is None
