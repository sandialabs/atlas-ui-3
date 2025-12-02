"""Unit tests for security check service."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.core.security_check import (
    SecurityCheckService,
    SecurityCheckResponse,
    SecurityCheckResult,
    get_security_check_service,
)
from backend.modules.config.config_manager import AppSettings


class TestSecurityCheckResponse:
    """Test SecurityCheckResponse class."""

    def test_blocked_status(self):
        """Test blocked status detection."""
        response = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Content blocked"
        )
        assert response.is_blocked()
        assert not response.has_warnings()
        assert not response.is_good()

    def test_warning_status(self):
        """Test warning status detection."""
        response = SecurityCheckResponse(
            status=SecurityCheckResult.ALLOWED_WITH_WARNINGS,
            message="Content has warnings"
        )
        assert not response.is_blocked()
        assert response.has_warnings()
        assert not response.is_good()

    def test_good_status(self):
        """Test good status detection."""
        response = SecurityCheckResponse(
            status=SecurityCheckResult.GOOD
        )
        assert not response.is_blocked()
        assert not response.has_warnings()
        assert response.is_good()

    def test_response_with_details(self):
        """Test response with additional details."""
        details = {"risk_score": 0.8, "categories": ["offensive"]}
        response = SecurityCheckResponse(
            status=SecurityCheckResult.BLOCKED,
            message="Offensive content detected",
            details=details
        )
        assert response.details == details
        assert response.message == "Offensive content detected"


class TestSecurityCheckService:
    """Test SecurityCheckService class."""

    def _create_service(
        self,
        input_enabled=True,
        output_enabled=True,
        api_url="http://test-api.example.com/check",
        api_key="test-key-123",
        timeout=10
    ):
        """Helper to create SecurityCheckService for testing."""
        app_settings = MagicMock(spec=AppSettings)
        app_settings.feature_security_check_input_enabled = input_enabled
        app_settings.feature_security_check_output_enabled = output_enabled
        app_settings.security_check_api_url = api_url
        app_settings.security_check_api_key = api_key
        app_settings.security_check_timeout = timeout
        return SecurityCheckService(app_settings)

    @pytest.mark.asyncio
    async def test_input_check_disabled(self):
        """Test input check when feature is disabled."""
        service = self._create_service(input_enabled=False)
        
        result = await service.check_input("test content", user_email="test@test.com")
        
        assert result.is_good()
        assert result.status == SecurityCheckResult.GOOD

    @pytest.mark.asyncio
    async def test_output_check_disabled(self):
        """Test output check when feature is disabled."""
        service = self._create_service(output_enabled=False)
        
        result = await service.check_output("test content", user_email="test@test.com")
        
        assert result.is_good()
        assert result.status == SecurityCheckResult.GOOD

    @pytest.mark.asyncio
    async def test_check_without_api_config(self):
        """Test check when API is not configured."""
        service = self._create_service(api_url=None, api_key=None)
        
        result = await service.check_input("test content", user_email="test@test.com")
        
        assert result.is_good()
        assert result.status == SecurityCheckResult.GOOD

    @pytest.mark.asyncio
    async def test_input_check_blocked(self):
        """Test input check when content is blocked."""
        service = self._create_service()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "blocked",
            "message": "Offensive content detected",
            "details": {"risk_score": 0.9}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await service.check_input(
                content="bad content",
                user_email="test@test.com"
            )
            
            assert result.is_blocked()
            assert result.message == "Offensive content detected"
            assert result.details["risk_score"] == 0.9

    @pytest.mark.asyncio
    async def test_input_check_with_warnings(self):
        """Test input check when content has warnings."""
        service = self._create_service()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "allowed-with-warnings",
            "message": "Content may be sensitive",
            "details": {"warnings": ["potentially offensive"]}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await service.check_input(
                content="questionable content",
                user_email="test@test.com"
            )
            
            assert result.has_warnings()
            assert result.message == "Content may be sensitive"

    @pytest.mark.asyncio
    async def test_input_check_good(self):
        """Test input check when content is good."""
        service = self._create_service()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "good",
            "message": None,
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await service.check_input(
                content="good content",
                user_email="test@test.com"
            )
            
            assert result.is_good()

    @pytest.mark.asyncio
    async def test_output_check_blocked(self):
        """Test output check when content is blocked."""
        service = self._create_service()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "blocked",
            "message": "Response contains sensitive information",
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await service.check_output(
                content="sensitive response",
                user_email="test@test.com"
            )
            
            assert result.is_blocked()
            assert result.message == "Response contains sensitive information"

    @pytest.mark.asyncio
    async def test_check_with_message_history(self):
        """Test check with message history context."""
        service = self._create_service()
        
        message_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"}
        ]
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "good",
            "message": None,
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post
            
            await service.check_input(
                content="test content",
                message_history=message_history,
                user_email="test@test.com"
            )
            
            # Verify message history was sent in payload
            call_args = mock_post.call_args
            assert call_args is not None
            payload = call_args[1]["json"]
            assert payload["message_history"] == message_history
            assert payload["username"] == "test@test.com"
            assert payload["check_type"] == "input"

    @pytest.mark.asyncio
    async def test_check_api_error_fallback(self):
        """Test fallback to allow when API returns error."""
        service = self._create_service()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("API error")
            )
            
            result = await service.check_input(
                content="test content",
                user_email="test@test.com"
            )
            
            # Should default to GOOD on error
            assert result.is_good()

    @pytest.mark.asyncio
    async def test_check_invalid_status_fallback(self):
        """Test fallback when API returns invalid status."""
        service = self._create_service()
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "invalid-status",
            "message": "Test",
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            
            result = await service.check_input(
                content="test content",
                user_email="test@test.com"
            )
            
            # Should default to GOOD on invalid status
            assert result.is_good()

    @pytest.mark.asyncio
    async def test_check_timeout_configuration(self):
        """Test that timeout is properly configured."""
        service = self._create_service(timeout=5)
        
        # Verify timeout is set from settings
        assert service.timeout == 5
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "good",
            "message": None,
            "details": {}
        }
        mock_response.raise_for_status = MagicMock()
        
        with patch("core.security_check.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock(return_value=mock_response)
            mock_client.return_value.__aenter__.return_value.post = mock_post
            
            await service.check_input(
                content="test content",
                user_email="test@test.com"
            )
            
            # Verify timeout was used
            call_args = mock_post.call_args
            assert call_args[1]["timeout"] == 5


class TestGetSecurityCheckService:
    """Test get_security_check_service factory function."""

    def test_factory_creates_service(self):
        """Test factory function creates service instance."""
        app_settings = MagicMock(spec=AppSettings)
        app_settings.feature_security_check_input_enabled = True
        app_settings.feature_security_check_output_enabled = True
        app_settings.security_check_api_url = "http://test.com"
        app_settings.security_check_api_key = "key"
        app_settings.security_check_timeout = 10
        
        service = get_security_check_service(app_settings)
        
        assert isinstance(service, SecurityCheckService)
        assert service.app_settings == app_settings
