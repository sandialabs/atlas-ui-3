"""Tests for RAG client configuration and HTTP client behavior."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from modules.rag.client import RAGClient
from core.http_client import RAGHTTPClient, create_rag_client


class TestRAGClientConfiguration:
    """Test RAG client configuration and mode selection."""
    
    def test_rag_client_http_mode_when_mock_rag_false(self):
        """When MOCK_RAG=false, RAGClient should use HTTP mode and not setup test client."""
        mock_app_settings = MagicMock()
        mock_app_settings.mock_rag = False
        mock_app_settings.rag_url = "http://localhost:8001"
        
        # Patch config_manager at the modules.config level where it's imported from
        with patch('modules.config.config_manager') as mock_config:
            mock_config.app_settings = mock_app_settings
            
            with patch('modules.rag.client.create_rag_client') as mock_create:
                mock_http_client = MagicMock()
                mock_create.return_value = mock_http_client
                
                client = RAGClient()
                
                # Verify HTTP mode is set
                assert client.mock_mode is False
                assert client.base_url == "http://localhost:8001"
                
                # Verify test client is NOT initialized
                assert client.test_client is None
                
                # Verify HTTP client was created
                mock_create.assert_called_once_with("http://localhost:8001", 30.0)
                assert client.http_client == mock_http_client
    
    def test_rag_client_mock_mode_when_mock_rag_true(self):
        """When MOCK_RAG=true, RAGClient should use in-process mock mode."""
        mock_app_settings = MagicMock()
        mock_app_settings.mock_rag = True
        mock_app_settings.rag_url = "http://localhost:8001"
        
        with patch('modules.config.config_manager') as mock_config:
            mock_config.app_settings = mock_app_settings
            
            with patch('modules.rag.client.create_rag_client') as mock_create:
                mock_http_client = MagicMock()
                mock_create.return_value = mock_http_client
                
                # Mock the test client setup
                with patch.object(RAGClient, '_setup_test_client') as mock_setup:
                    client = RAGClient()
                    
                    # Verify mock mode is set
                    assert client.mock_mode is True
                    assert client.base_url == "http://localhost:8001"
                    
                    # Verify test client setup was attempted
                    mock_setup.assert_called_once()
    
    def test_rag_url_setting_used_as_base_url(self):
        """Verify that rag_url setting is used as the HTTP client base URL."""
        custom_url = "http://my-rag-server:9999"
        mock_app_settings = MagicMock()
        mock_app_settings.mock_rag = False
        mock_app_settings.rag_url = custom_url
        
        with patch('modules.config.config_manager') as mock_config:
            mock_config.app_settings = mock_app_settings
            
            with patch('modules.rag.client.create_rag_client') as mock_create:
                RAGClient()
                
                # Verify custom URL was passed to create_rag_client
                mock_create.assert_called_once_with(custom_url, 30.0)


class TestRAGHTTPClient:
    """Test the real HTTPx-based RAG HTTP client."""
    
    def test_rag_http_client_initialization(self):
        """Test RAGHTTPClient initializes with correct base URL and timeout."""
        client = create_rag_client("http://example.com:8080", 15.0)
        
        assert isinstance(client, RAGHTTPClient)
        assert client.base_url == "http://example.com:8080"
        assert client.timeout == 15.0
    
    def test_rag_http_client_strips_trailing_slash(self):
        """Test RAGHTTPClient removes trailing slash from base URL."""
        client = create_rag_client("http://example.com/", 30.0)
        
        assert client.base_url == "http://example.com"
    
    @pytest.mark.asyncio
    async def test_rag_http_client_get_success(self):
        """Test successful GET request to RAG service."""
        client = RAGHTTPClient("http://example.com", 30.0)
        
        mock_response = MagicMock()
        mock_response.json.return_value = {"accessible_data_sources": [{"name": "test", "compliance_level": "public"}]}
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            
            result = await client.get("/v1/discover/datasources/user@test.com")
            
            assert result == {"accessible_data_sources": [{"name": "test", "compliance_level": "public"}]}
            mock_get.assert_called_once_with("http://example.com/v1/discover/datasources/user@test.com")
    
    @pytest.mark.asyncio
    async def test_rag_http_client_post_success(self):
        """Test successful POST request to RAG service."""
        client = RAGHTTPClient("http://example.com", 30.0)
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "RAG response"}}],
            "rag_metadata": {"query_processing_time_ms": 100}
        }
        
        payload = {"messages": [{"role": "user", "content": "test"}], "model": "gpt-4"}
        
        with patch.object(client.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            
            result = await client.post("/v1/chat/completions", json_data=payload)
            
            assert result["choices"][0]["message"]["content"] == "RAG response"
            mock_post.assert_called_once_with("http://example.com/v1/chat/completions", json=payload)
    
    @pytest.mark.asyncio
    async def test_rag_http_client_get_http_error(self):
        """Test GET request handles HTTP errors properly."""
        from fastapi import HTTPException
        import httpx
        
        client = RAGHTTPClient("http://example.com", 30.0)
        
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"
        
        with patch.object(client.client, 'get', new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.HTTPStatusError(
                "Not found", 
                request=MagicMock(), 
                response=mock_response
            )
            
            with pytest.raises(HTTPException) as exc_info:
                await client.get("/v1/discover/datasources/user@test.com")
            
            assert exc_info.value.status_code == 404
            assert "RAG service error" in exc_info.value.detail
    
    @pytest.mark.asyncio
    async def test_rag_http_client_post_connection_error(self):
        """Test POST request handles connection errors properly."""
        from fastapi import HTTPException
        import httpx
        
        client = RAGHTTPClient("http://example.com", 30.0)
        
        with patch.object(client.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.ConnectError("Connection refused")
            
            with pytest.raises(HTTPException) as exc_info:
                await client.post("/v1/chat/completions", json_data={})
            
            assert exc_info.value.status_code == 503
            assert "RAG service unavailable" in exc_info.value.detail
    
    @pytest.mark.asyncio
    async def test_discover_data_sources_uses_http_client_when_mock_false(self):
        """Test that discover_data_sources uses HTTP client when MOCK_RAG=false."""
        mock_app_settings = MagicMock()
        mock_app_settings.mock_rag = False
        mock_app_settings.rag_url = "http://localhost:8001"
        
        with patch('modules.config.config_manager') as mock_config:
            mock_config.app_settings = mock_app_settings
            
            with patch('modules.rag.client.create_rag_client') as mock_create:
                mock_http_client = AsyncMock()
                mock_http_client.get = AsyncMock(return_value={
                    "accessible_data_sources": [
                        {"name": "source1", "compliance_level": "public"}
                    ]
                })
                mock_create.return_value = mock_http_client
                
                client = RAGClient()
                result = await client.discover_data_sources("test@test.com")
                
                # Verify HTTP client was used, not test client
                mock_http_client.get.assert_called_once_with("/v1/discover/datasources/test@test.com")
                assert len(result) == 1
                assert result[0].name == "source1"
                assert result[0].compliance_level == "public"


class TestRAGClientEnvironmentConfiguration:
    """Test RAG client respects environment configuration."""
    
    def test_rag_url_env_var_support(self):
        """Test that RAG_URL environment variable is supported."""
        # This is tested implicitly through config_manager's validation_alias
        # The Field definition in config_manager.py should have:
        # validation_alias=AliasChoices("RAG_URL")
        # This test documents the expected behavior
        pass
    
    def test_mock_rag_env_var_controls_mode(self):
        """Test that MOCK_RAG environment variable controls client mode."""
        # This is tested through the client initialization tests above
        # Documenting that MOCK_RAG=false should result in HTTP mode
        # and MOCK_RAG=true should result in TestClient mode
        pass
