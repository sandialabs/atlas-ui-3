"""Tests for generic MCP tool result parsing.

These tests verify that Atlas can parse tool results from both:
1. MCP responses with structuredContent field
2. MCP responses with data only in content[0].text (without structuredContent)
"""

import json
import pytest
from unittest.mock import patch, AsyncMock, Mock

from backend.modules.mcp_tools.client import MCPToolManager
from domain.messages.models import ToolCall


class MockTextContent:
    """Mock for MCP text content item."""
    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class MockMCPResultWithStructuredContent:
    """Mock MCP result that includes structured_content field."""
    def __init__(self, payload: dict):
        self.content = [MockTextContent(json.dumps(payload))]
        self.structured_content = payload
        self.data = None
        self.is_error = False
        self.meta = None


class MockMCPResultWithoutStructuredContent:
    """Mock MCP result that only has data in content[0].text (no structured_content)."""
    def __init__(self, payload: dict):
        self.content = [MockTextContent(json.dumps(payload))]
        self.structured_content = None
        self.data = None
        self.is_error = False
        self.meta = None


class TestMCPToolResultParsing:
    """Tests for parsing MCP tool results."""

    def _create_screenshot_payload(self):
        """Create a sample screenshot tool response payload."""
        return {
            "results": {"content": "Screenshot captured successfully"},
            "artifacts": [
                {
                    "name": "screenshot.jpg",
                    "b64": "/9j/4AAQSkZJRgABAQEAkACQAAD...",
                    "mime": "image/jpeg",
                    "size": 160805,
                    "description": "screenshot",
                    "viewer": "image"
                }
            ],
            "display": {
                "open_canvas": True,
                "primary_file": "screenshot.jpg",
                "mode": "replace",
                "viewer_hint": "image",
                "title": "screenshot.jpg"
            },
            "meta_data": {
                "capture_time": "2024-01-15T10:30:00Z"
            }
        }

    def test_normalize_mcp_tool_result_with_structured_content(self):
        """Test _normalize_mcp_tool_result with structured_content."""
        manager = MCPToolManager.__new__(MCPToolManager)
        payload = self._create_screenshot_payload()
        raw_result = MockMCPResultWithStructuredContent(payload)
        
        normalized = manager._normalize_mcp_tool_result(raw_result)
        
        assert "results" in normalized
        assert normalized["results"]["content"] == "Screenshot captured successfully"

    def test_normalize_mcp_tool_result_without_structured_content(self):
        """Test _normalize_mcp_tool_result falls back to parsing content[0].text."""
        manager = MCPToolManager.__new__(MCPToolManager)
        payload = self._create_screenshot_payload()
        raw_result = MockMCPResultWithoutStructuredContent(payload)
        
        normalized = manager._normalize_mcp_tool_result(raw_result)
        
        assert "results" in normalized
        assert normalized["results"]["content"] == "Screenshot captured successfully"

    @pytest.mark.asyncio
    async def test_execute_tool_extracts_artifacts_from_structured_content(self):
        """Test execute_tool extracts artifacts when structured_content is present."""
        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance

                payload = self._create_screenshot_payload()
                mock_result = MockMCPResultWithStructuredContent(payload)
                mock_client_instance.call_tool = AsyncMock(return_value=mock_result)

                await manager.initialize_clients()

                tool_call = ToolCall(id="call_1", name="test-server_capture", arguments={})
                mock_tool = Mock()
                mock_tool.name = "capture"
                manager._tool_index = {
                    "test-server_capture": {
                        'server': 'test-server',
                        'tool': mock_tool
                    }
                }

                result = await manager.execute_tool(tool_call)

                assert result.success is True
                assert len(result.artifacts) == 1
                assert result.artifacts[0]["name"] == "screenshot.jpg"
                assert result.artifacts[0]["b64"] is not None
                assert result.display_config is not None
                assert result.display_config["open_canvas"] is True
                assert result.meta_data is not None
                assert result.meta_data["capture_time"] == "2024-01-15T10:30:00Z"

    @pytest.mark.asyncio
    async def test_execute_tool_extracts_artifacts_from_content_text_fallback(self):
        """Test execute_tool extracts artifacts when only content[0].text is available.
        
        This is the key fix: artifacts/display/meta_data should be extracted
        even when structured_content is not present in the MCP response.
        """
        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance

                payload = self._create_screenshot_payload()
                # Use result WITHOUT structured_content - only has data in content[0].text
                mock_result = MockMCPResultWithoutStructuredContent(payload)
                mock_client_instance.call_tool = AsyncMock(return_value=mock_result)

                await manager.initialize_clients()

                tool_call = ToolCall(id="call_1", name="test-server_capture", arguments={})
                mock_tool = Mock()
                mock_tool.name = "capture"
                manager._tool_index = {
                    "test-server_capture": {
                        'server': 'test-server',
                        'tool': mock_tool
                    }
                }

                result = await manager.execute_tool(tool_call)

                assert result.success is True
                # These assertions verify the fix - artifacts/display/metadata
                # should be extracted even without structured_content
                assert len(result.artifacts) == 1
                assert result.artifacts[0]["name"] == "screenshot.jpg"
                assert result.artifacts[0]["b64"] is not None
                assert result.display_config is not None
                assert result.display_config["open_canvas"] is True
                assert result.meta_data is not None
                assert result.meta_data["capture_time"] == "2024-01-15T10:30:00Z"

    @pytest.mark.asyncio
    async def test_execute_tool_handles_empty_content(self):
        """Test execute_tool handles result with no parseable content gracefully."""
        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance

                # Create result with non-JSON text content
                class MockPlainResult:
                    content = [MockTextContent("Just a plain text result")]
                    structured_content = None
                    data = None
                    is_error = False

                mock_client_instance.call_tool = AsyncMock(return_value=MockPlainResult())

                await manager.initialize_clients()

                tool_call = ToolCall(id="call_1", name="test-server_plain", arguments={})
                mock_tool = Mock()
                mock_tool.name = "plain"
                manager._tool_index = {
                    "test-server_plain": {
                        'server': 'test-server',
                        'tool': mock_tool
                    }
                }

                result = await manager.execute_tool(tool_call)

                # Should succeed but with no artifacts/display/metadata
                assert result.success is True
                assert result.artifacts == []
                assert result.display_config is None
                assert result.meta_data is None

    @pytest.mark.asyncio
    async def test_execute_tool_handles_malformed_json_in_content(self):
        """Test execute_tool handles malformed JSON in content[0].text gracefully."""
        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance

                # Create result with malformed JSON
                class MockMalformedResult:
                    content = [MockTextContent("{invalid json: true")]
                    structured_content = None
                    data = None
                    is_error = False

                mock_client_instance.call_tool = AsyncMock(return_value=MockMalformedResult())

                await manager.initialize_clients()

                tool_call = ToolCall(id="call_1", name="test-server_malformed", arguments={})
                mock_tool = Mock()
                mock_tool.name = "malformed"
                manager._tool_index = {
                    "test-server_malformed": {
                        'server': 'test-server',
                        'tool': mock_tool
                    }
                }

                result = await manager.execute_tool(tool_call)

                # Should still succeed but with no extracted artifacts
                assert result.success is True
                assert result.artifacts == []
                assert result.display_config is None
                assert result.meta_data is None
