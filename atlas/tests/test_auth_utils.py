"""Tests for auth_utils module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.core.authorization_manager import AuthorizationManager, create_authorization_manager


class TestAuthorizationManager:
    """Test suite for AuthorizationManager class."""

    @pytest.fixture
    def mock_auth_check_func(self):
        """Create a mock auth check function."""
        return AsyncMock()

    @pytest.fixture
    def mock_app_settings(self):
        """Create mock app settings."""
        settings = MagicMock()
        settings.admin_group = "admin"
        return settings

    @pytest.fixture
    def auth_manager(self, mock_auth_check_func, mock_app_settings):
        """Create an AuthorizationManager instance with mocked dependencies."""
        with patch('atlas.core.authorization_manager.get_app_settings', return_value=mock_app_settings):
            return AuthorizationManager(mock_auth_check_func)

    @pytest.mark.asyncio
    async def test_is_admin_returns_true_for_admin_user(self, auth_manager, mock_auth_check_func):
        """Test that is_admin returns True when auth check function returns True."""
        # Arrange
        user_email = "admin@example.com"
        mock_auth_check_func.return_value = True

        # Act
        result = await auth_manager.is_admin(user_email)

        # Assert
        assert result is True
        mock_auth_check_func.assert_called_once_with(user_email, "admin")

    @pytest.mark.asyncio
    async def test_is_admin_returns_false_for_non_admin_user(self, auth_manager, mock_auth_check_func):
        """Test that is_admin returns False when auth check function returns False."""
        # Arrange
        user_email = "user@example.com"
        mock_auth_check_func.return_value = False

        # Act
        result = await auth_manager.is_admin(user_email)

        # Assert
        assert result is False
        mock_auth_check_func.assert_called_once_with(user_email, "admin")

    @pytest.mark.asyncio
    async def test_is_admin_uses_correct_admin_group(self, mock_auth_check_func):
        """Test that is_admin uses the admin group from app settings."""
        # Arrange
        custom_admin_group = "super_admin"
        mock_settings = MagicMock()
        mock_settings.admin_group = custom_admin_group

        with patch('atlas.core.authorization_manager.get_app_settings', return_value=mock_settings):
            auth_manager = AuthorizationManager(mock_auth_check_func)

        user_email = "admin@example.com"
        mock_auth_check_func.return_value = True

        # Act
        await auth_manager.is_admin(user_email)

        # Assert
        mock_auth_check_func.assert_called_once_with(user_email, custom_admin_group)

    @pytest.mark.asyncio
    async def test_is_admin_handles_auth_check_exception(self, auth_manager, mock_auth_check_func):
        """Test that is_admin properly propagates exceptions from auth check function."""
        # Arrange
        user_email = "user@example.com"
        mock_auth_check_func.side_effect = Exception("Auth service unavailable")

        # Act & Assert
        with pytest.raises(Exception, match="Auth service unavailable"):
            await auth_manager.is_admin(user_email)

    @pytest.mark.asyncio
    async def test_is_admin_with_empty_email(self, auth_manager, mock_auth_check_func):
        """Test that is_admin handles empty email addresses."""
        # Arrange
        user_email = ""
        mock_auth_check_func.return_value = False

        # Act
        result = await auth_manager.is_admin(user_email)

        # Assert
        assert result is False
        mock_auth_check_func.assert_called_once_with("", "admin")

    @pytest.mark.asyncio
    async def test_is_admin_with_none_email(self, auth_manager, mock_auth_check_func):
        """Test that is_admin handles None email addresses."""
        # Arrange
        user_email = None
        mock_auth_check_func.return_value = False

        # Act
        result = await auth_manager.is_admin(user_email)

        # Assert
        assert result is False
        mock_auth_check_func.assert_called_once_with(None, "admin")


class TestCreateAuthorizationManager:
    """Test suite for create_authorization_manager factory function."""

    def test_create_authorization_manager_returns_instance(self):
        """Test that factory function returns an AuthorizationManager instance."""
        # Arrange
        mock_auth_check_func = AsyncMock()

        # Act
        with patch('atlas.core.authorization_manager.get_app_settings'):
            result = create_authorization_manager(mock_auth_check_func)

        # Assert
        assert isinstance(result, AuthorizationManager)
        assert result.auth_check_func is mock_auth_check_func

    def test_create_authorization_manager_initializes_app_settings(self):
        """Test that factory function properly initializes app settings."""
        # Arrange
        mock_auth_check_func = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.admin_group = "test_admin"

        # Act
        with patch('atlas.core.authorization_manager.get_app_settings', return_value=mock_settings) as mock_get_settings:
            result = create_authorization_manager(mock_auth_check_func)

        # Assert
        mock_get_settings.assert_called_once()
        assert result.app_settings is mock_settings

    @pytest.mark.asyncio
    async def test_created_manager_works_correctly(self):
        """Test that the manager created by factory function works correctly."""
        # Arrange
        mock_auth_check_func = AsyncMock(return_value=True)
        mock_settings = MagicMock()
        mock_settings.admin_group = "admin"

        # Act
        with patch('atlas.core.authorization_manager.get_app_settings', return_value=mock_settings):
            manager = create_authorization_manager(mock_auth_check_func)
            result = await manager.is_admin("test@example.com")

        # Assert
        assert result is True
        mock_auth_check_func.assert_called_once_with("test@example.com", "admin")
