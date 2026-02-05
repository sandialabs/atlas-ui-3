import pytest
from backend.modules.mcp_tools.client import MCPToolManager
from unittest.mock import patch, AsyncMock, Mock, ANY


@pytest.mark.integration
class TestMCPAuthenticationIntegration:
    """Integration tests for MCP authentication."""

    @pytest.mark.asyncio
    async def test_authenticated_connection_success(self, monkeypatch):
        """Should successfully connect to authenticated MCP server."""
        monkeypatch.setenv("MCP_TEST_TOKEN", "test-api-key-123")

        # Configure test server with auth requirement
        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
            "auth_token": "${MCP_TEST_TOKEN}"
        }

        # Mock config_manager to return our test server config
        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"mcp-http-mock": Mock()}
            mock_config_manager.mcp_config.servers["mcp-http-mock"].model_dump.return_value = server_config
            mock_config_manager.app_settings.mcp_call_timeout = 120
            mock_config_manager.app_settings.mcp_discovery_timeout = 30
            
            manager = MCPToolManager()
            manager.servers_config = {"mcp-http-mock": server_config}
            
            # Mock the fastmcp.Client to avoid actual network call for now
            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance
                mock_client_instance.list_tools.return_value = [] # Mock an empty list of tools

                await manager.initialize_clients()
                
                # Assert that the client was initialized and added to manager.clients
                assert "mcp-http-mock" in manager.clients
                MockFastMCPClient.assert_called_once_with(
                    "http://localhost:8001/mcp",
                    auth="test-api-key-123",
                    log_handler=ANY,
                    elicitation_handler=ANY,
                    sampling_handler=ANY,
                )

    @pytest.mark.asyncio
    async def test_authenticated_connection_failure_invalid_token(self, monkeypatch):
        """Should fail to connect with invalid token."""
        monkeypatch.setenv("MCP_TEST_TOKEN", "invalid-token") # Set an invalid token

        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
            "auth_token": "${MCP_TEST_TOKEN}"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"mcp-http-mock": Mock()}
            mock_config_manager.mcp_config.servers["mcp-http-mock"].model_dump.return_value = server_config
            mock_config_manager.app_settings.mcp_call_timeout = 120
            mock_config_manager.app_settings.mcp_discovery_timeout = 30

            manager = MCPToolManager()
            manager.servers_config = {"mcp-http-mock": server_config}

            # Mock the fastmcp.Client to simulate an authentication error
            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.side_effect = Exception("Authentication failed: 401 Unauthorized")

                await manager.initialize_clients()

                # Client object is created successfully (not connected yet)
                assert "mcp-http-mock" in manager.clients
                # Verify auth token was passed correctly
                MockFastMCPClient.assert_called_once_with(
                    "http://localhost:8001/mcp",
                    auth="invalid-token",
                    log_handler=ANY,
                    elicitation_handler=ANY,
                    sampling_handler=ANY,
                )

                # Now try to discover tools - this should fail due to auth error
                await manager.discover_tools()

                # After failed connection, tools should not be discovered
                if not hasattr(manager, '_tool_index'):
                    manager._tool_index = {}
                assert len([k for k in manager._tool_index.keys() if k.startswith("mcp-http-mock_")]) == 0

    @pytest.mark.asyncio
    async def test_authenticated_tool_execution(self, monkeypatch):
        """Should successfully execute tool after authentication."""
        monkeypatch.setenv("MCP_TEST_TOKEN", "test-api-key-123")

        server_config = {
            "url": "http://localhost:8001/mcp",
            "transport": "http",
            "auth_token": "${MCP_TEST_TOKEN}"
        }

        with patch('backend.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"mcp-http-mock": Mock()}
            mock_config_manager.mcp_config.servers["mcp-http-mock"].model_dump.return_value = server_config
            mock_config_manager.app_settings.mcp_call_timeout = 120
            mock_config_manager.app_settings.mcp_discovery_timeout = 30

            manager = MCPToolManager()
            manager.servers_config = {"mcp-http-mock": server_config}

            # Mock the fastmcp.Client and its call_tool method
            with patch('backend.modules.mcp_tools.client.Client') as MockFastMCPClient:
                mock_client_instance = MockFastMCPClient.return_value
                mock_client_instance.__aenter__.return_value = mock_client_instance
                mock_client_instance.list_tools.return_value = [] # Mock an empty list of tools

                # Make call_tool return an async result
                class MockResult:
                    data = {"results": "tool_result"}
                mock_client_instance.call_tool = AsyncMock(return_value=MockResult())

                await manager.initialize_clients()
                
                # Simulate tool call
                from domain.messages.models import ToolCall
                tool_call = ToolCall(id="call_1", name="mcp-http-mock_test_tool", arguments={})
                
                # Need to mock the _tool_index for execute_tool to find the server
                mock_tool = Mock()
                mock_tool.name = "test_tool"
                manager._tool_index = {
                    "mcp-http-mock_test_tool": {
                        'server': 'mcp-http-mock',
                        'tool': mock_tool
                    }
                }

                result = await manager.execute_tool(tool_call)
                
                assert result.success is True
                assert "tool_result" in result.content
                mock_client_instance.call_tool.assert_called_once()
                call_args = mock_client_instance.call_tool.call_args
                assert call_args[0] == ("test_tool", {})
                assert "progress_handler" in call_args[1]
