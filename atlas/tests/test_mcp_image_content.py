"""Tests for ImageContent handling in MCP tool results.

These tests verify that Atlas can extract and process ImageContent items
from MCP tool responses and convert them to artifacts for display.
"""

import pytest
from unittest.mock import AsyncMock, patch

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.domain.messages.models import ToolCall


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
            assert artifact["name"] == "mcp_image_0.png"
            assert artifact["b64"] == image_b64
            assert artifact["mime"] == "image/png"
            assert artifact["viewer"] == "image"
            assert "generate_image" in artifact["description"]
            
            # Verify display config was auto-created
            assert result.display_config is not None
            assert result.display_config["primary_file"] == "mcp_image_0.png"
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
        
        # Use valid base64 encoded strings
        images = [
            {"data": "aW1hZ2UgZGF0YSAxCg==", "mime": "image/png"},
            {"data": "aW1hZ2UgZGF0YSAyCg==", "mime": "image/jpeg"},
            {"data": "aW1hZ2UgZGF0YSAzCg==", "mime": "image/gif"}
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
                assert artifact["name"] == f"mcp_image_{i}.{expected_ext}"
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
        # Use valid base64 encoded string
        image_b64 = "aW1hZ2VkYXRhCg=="
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
            assert artifact["name"] == "mcp_image_0.png"
            assert artifact["b64"] == image_b64

            # Verify the text content was extracted and included in result.content
            # The content should be JSON containing the text in "results"
            import json
            content_dict = json.loads(result.content)
            assert "results" in content_dict
            assert text in content_dict["results"]

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

    @pytest.mark.asyncio
    async def test_image_content_missing_data(self):
        """Test that ImageContent with None/missing data is skipped."""
        manager = MCPToolManager.__new__(MCPToolManager)

        class MockTool:
            def __init__(self, name):
                self.name = name

        tool_call = ToolCall(
            id="test-call-5",
            name="missing_data",
            arguments={}
        )

        # Create ImageContent with missing data
        class MockImageContentNoData:
            def __init__(self):
                self.type = "image"
                self.data = None
                self.mimeType = "image/png"

        class MockResult:
            def __init__(self):
                self.content = [MockImageContentNoData()]
                self.structured_content = None
                self.data = None
                self.is_error = False

        raw_result = MockResult()

        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "missing_data": {
                    "server": "test-server",
                    "tool": MockTool("missing_data")
                }
            }

            result = await manager.execute_tool(tool_call, context={})

            # No artifacts should be created when data is missing
            assert len(result.artifacts) == 0

    @pytest.mark.asyncio
    async def test_image_content_missing_mime_type(self):
        """Test that ImageContent with None/missing mimeType is skipped."""
        manager = MCPToolManager.__new__(MCPToolManager)

        class MockTool:
            def __init__(self, name):
                self.name = name

        tool_call = ToolCall(
            id="test-call-6",
            name="missing_mime",
            arguments={}
        )

        # Create ImageContent with missing mime type
        class MockImageContentNoMime:
            def __init__(self):
                self.type = "image"
                self.data = "SGVsbG8gV29ybGQ="  # Valid base64
                self.mimeType = None

        class MockResult:
            def __init__(self):
                self.content = [MockImageContentNoMime()]
                self.structured_content = None
                self.data = None
                self.is_error = False

        raw_result = MockResult()

        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "missing_mime": {
                    "server": "test-server",
                    "tool": MockTool("missing_mime")
                }
            }

            result = await manager.execute_tool(tool_call, context={})

            # No artifacts should be created when mimeType is missing
            assert len(result.artifacts) == 0

    @pytest.mark.asyncio
    async def test_image_content_invalid_mime_type(self):
        """Test that ImageContent with unsupported mime type is skipped."""
        manager = MCPToolManager.__new__(MCPToolManager)

        class MockTool:
            def __init__(self, name):
                self.name = name

        tool_call = ToolCall(
            id="test-call-7",
            name="bad_mime",
            arguments={}
        )

        # Create ImageContent with unsupported mime type
        class MockImageContentBadMime:
            def __init__(self):
                self.type = "image"
                self.data = "SGVsbG8gV29ybGQ="  # Valid base64
                self.mimeType = "application/octet-stream"

        class MockResult:
            def __init__(self):
                self.content = [MockImageContentBadMime()]
                self.structured_content = None
                self.data = None
                self.is_error = False

        raw_result = MockResult()

        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "bad_mime": {
                    "server": "test-server",
                    "tool": MockTool("bad_mime")
                }
            }

            result = await manager.execute_tool(tool_call, context={})

            # No artifacts should be created for unsupported mime type
            assert len(result.artifacts) == 0

    @pytest.mark.asyncio
    async def test_image_content_invalid_base64(self):
        """Test that ImageContent with invalid base64 data is skipped."""
        manager = MCPToolManager.__new__(MCPToolManager)

        class MockTool:
            def __init__(self, name):
                self.name = name

        tool_call = ToolCall(
            id="test-call-8",
            name="bad_base64",
            arguments={}
        )

        # Create ImageContent with invalid base64
        class MockImageContentBadB64:
            def __init__(self):
                self.type = "image"
                self.data = "not-valid-base64!!!"
                self.mimeType = "image/png"

        class MockResult:
            def __init__(self):
                self.content = [MockImageContentBadB64()]
                self.structured_content = None
                self.data = None
                self.is_error = False

        raw_result = MockResult()

        with patch.object(manager, 'call_tool', new_callable=AsyncMock) as mock_call:
            mock_call.return_value = raw_result
            manager._tool_index = {
                "bad_base64": {
                    "server": "test-server",
                    "tool": MockTool("bad_base64")
                }
            }

            result = await manager.execute_tool(tool_call, context={})

            # No artifacts should be created for invalid base64
            assert len(result.artifacts) == 0
