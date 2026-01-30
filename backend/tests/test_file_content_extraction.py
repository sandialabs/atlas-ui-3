"""Tests for file content extraction feature.

Tests the FileExtractorConfig, FileExtractorsConfig models, FileContentExtractor class,
and related configuration functionality.
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
import httpx

from modules.config.config_manager import (
    ConfigManager,
    FileExtractorConfig,
    FileExtractorsConfig,
)
from modules.file_storage.content_extractor import (
    FileContentExtractor,
    ExtractionResult,
)


class TestFileExtractorConfig:
    """Test FileExtractorConfig Pydantic model."""

    def test_file_extractor_config_with_defaults(self):
        """FileExtractorConfig should have sensible defaults."""
        config = FileExtractorConfig(url="http://localhost:8010/extract")

        assert config.url == "http://localhost:8010/extract"
        assert config.method == "POST"
        assert config.timeout_seconds == 30
        assert config.max_file_size_mb == 50
        assert config.preview_chars == 2000
        assert config.request_format == "base64"
        assert config.response_field == "text"
        assert config.enabled is True

    def test_file_extractor_config_with_custom_values(self):
        """FileExtractorConfig should accept custom values."""
        config = FileExtractorConfig(
            url="http://custom-service:9000/ocr",
            method="PUT",
            timeout_seconds=120,
            max_file_size_mb=100,
            preview_chars=5000,
            request_format="url",
            response_field="content",
            enabled=False
        )

        assert config.url == "http://custom-service:9000/ocr"
        assert config.method == "PUT"
        assert config.timeout_seconds == 120
        assert config.max_file_size_mb == 100
        assert config.preview_chars == 5000
        assert config.request_format == "url"
        assert config.response_field == "content"
        assert config.enabled is False

    def test_file_extractor_config_preview_chars_optional(self):
        """preview_chars should be optional (None)."""
        config = FileExtractorConfig(
            url="http://localhost:8010/extract",
            preview_chars=None
        )

        assert config.preview_chars is None


class TestFileExtractorsConfig:
    """Test FileExtractorsConfig Pydantic model."""

    def test_file_extractors_config_with_defaults(self):
        """FileExtractorsConfig should have sensible defaults."""
        config = FileExtractorsConfig()

        assert config.enabled is True
        assert config.default_behavior == "full"
        assert config.extractors == {}
        assert config.extension_mapping == {}
        assert config.mime_mapping == {}

    def test_file_extractors_config_with_extractors(self):
        """FileExtractorsConfig should properly parse nested extractors."""
        config = FileExtractorsConfig(
            enabled=True,
            default_behavior="attach_only",
            extractors={
                "pdf-text": {
                    "url": "http://localhost:8010/extract",
                    "timeout_seconds": 60
                },
                "image-vision": {
                    "url": "http://localhost:8011/analyze",
                    "enabled": False
                }
            },
            extension_mapping={
                ".pdf": "pdf-text",
                ".png": "image-vision"
            },
            mime_mapping={
                "application/pdf": "pdf-text",
                "image/png": "image-vision"
            }
        )

        assert config.enabled is True
        assert config.default_behavior == "none"  # "attach_only" normalized to "none"
        assert len(config.extractors) == 2
        assert isinstance(config.extractors["pdf-text"], FileExtractorConfig)
        assert config.extractors["pdf-text"].url == "http://localhost:8010/extract"
        assert config.extractors["pdf-text"].timeout_seconds == 60
        assert config.extractors["image-vision"].enabled is False
        assert config.extension_mapping[".pdf"] == "pdf-text"
        assert config.mime_mapping["application/pdf"] == "pdf-text"

    def test_file_extractors_config_validator_converts_dicts(self):
        """Validator should convert plain dicts to FileExtractorConfig objects."""
        raw_data = {
            "enabled": True,
            "extractors": {
                "test": {"url": "http://test.local/extract"}
            }
        }

        config = FileExtractorsConfig(**raw_data)

        assert isinstance(config.extractors["test"], FileExtractorConfig)
        assert config.extractors["test"].url == "http://test.local/extract"


class TestFileContentExtractor:
    """Test FileContentExtractor class."""

    def test_extractor_initialization_with_config(self):
        """FileContentExtractor should accept config override."""
        config = FileExtractorsConfig(enabled=True)
        extractor = FileContentExtractor(config=config)

        assert extractor.config is config

    def test_extractor_lazy_loads_config(self):
        """FileContentExtractor should lazy load config if not provided."""
        extractor = FileContentExtractor()

        # Config should be loaded on first access
        config = extractor.config
        assert config is not None
        assert isinstance(config, FileExtractorsConfig)

    def test_is_enabled_checks_both_flags(self):
        """is_enabled should check both feature flag and config enabled."""
        config = FileExtractorsConfig(enabled=True)
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            # Both enabled
            mock_settings.return_value.feature_file_content_extraction_enabled = True
            assert extractor.is_enabled() is True

            # Feature flag disabled
            mock_settings.return_value.feature_file_content_extraction_enabled = False
            assert extractor.is_enabled() is False

    def test_is_enabled_config_disabled(self):
        """is_enabled should return False if config.enabled is False."""
        config = FileExtractorsConfig(enabled=False)
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True
            assert extractor.is_enabled() is False

    def test_get_default_behavior(self):
        """get_default_behavior should return config value."""
        config = FileExtractorsConfig(default_behavior="attach_only")
        extractor = FileContentExtractor(config=config)

        assert extractor.get_default_behavior() == "none"  # "attach_only" normalized

    def test_get_default_behavior_preview(self):
        """get_default_behavior should accept preview mode directly."""
        config = FileExtractorsConfig(default_behavior="preview")
        extractor = FileContentExtractor(config=config)

        assert extractor.get_default_behavior() == "preview"

    def test_legacy_extract_normalizes_to_full(self):
        """Legacy 'extract' value should normalize to 'full'."""
        config = FileExtractorsConfig(default_behavior="extract")

        assert config.default_behavior == "full"

    def test_legacy_attach_only_normalizes_to_none(self):
        """Legacy 'attach_only' value should normalize to 'none'."""
        config = FileExtractorsConfig(default_behavior="attach_only")

        assert config.default_behavior == "none"

    def test_get_extractor_for_file_by_extension(self):
        """Should find extractor by file extension."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = extractor.get_extractor_for_file("document.pdf")
            assert result is not None
            assert result.url == "http://localhost/pdf"

    def test_get_extractor_for_file_by_mime_fallback(self):
        """Should fall back to MIME type lookup."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={},  # No extension mapping
            mime_mapping={"application/pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = extractor.get_extractor_for_file("document.xyz", mime_type="application/pdf")
            assert result is not None
            assert result.url == "http://localhost/pdf"

    def test_get_extractor_for_file_returns_none_if_disabled(self):
        """Should return None if extraction is disabled."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=False)
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = extractor.get_extractor_for_file("document.pdf")
            assert result is None

    def test_get_extractor_for_file_returns_none_if_no_mapping(self):
        """Should return None if no mapping exists."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={},
            extension_mapping={}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = extractor.get_extractor_for_file("document.xyz")
            assert result is None

    def test_can_extract_returns_boolean(self):
        """can_extract should return True/False based on extractor availability."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            assert extractor.can_extract("document.pdf") is True
            assert extractor.can_extract("document.xyz") is False

    def test_get_supported_extensions(self):
        """get_supported_extensions should return list of extractable extensions."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True),
                "image-vision": FileExtractorConfig(url="http://localhost/img", enabled=False)
            },
            extension_mapping={
                ".pdf": "pdf-text",
                ".png": "image-vision"  # Disabled extractor
            }
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            extensions = extractor.get_supported_extensions()
            assert ".pdf" in extensions
            assert ".png" not in extensions  # Extractor is disabled

    def test_get_supported_extensions_empty_when_disabled(self):
        """get_supported_extensions should return empty list when disabled."""
        config = FileExtractorsConfig(enabled=False)
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            extensions = extractor.get_supported_extensions()
            assert extensions == []


class TestFileContentExtractorAsync:
    """Test FileContentExtractor async extraction methods."""

    @pytest.mark.asyncio
    async def test_extract_content_success(self):
        """extract_content should return successful result on 200 response."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    response_field="text",
                    preview_chars=100
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "text": "This is the extracted content from the PDF document.",
            "metadata": {"pages": 5}
        }

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdCBjb250ZW50"  # "test content" in base64
                )

                assert result.success is True
                assert result.content == "This is the extracted content from the PDF document."
                assert result.metadata == {"pages": 5}

    @pytest.mark.asyncio
    async def test_extract_content_no_extractor(self):
        """extract_content should return error when no extractor available."""
        config = FileExtractorsConfig(enabled=True, extractors={})
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = await extractor.extract_content(
                filename="document.xyz",
                content_base64="dGVzdA=="
            )

            assert result.success is False
            assert "No extractor available" in result.error

    @pytest.mark.asyncio
    async def test_extract_content_file_too_large(self):
        """extract_content should reject files exceeding size limit."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    max_file_size_mb=1  # 1MB limit
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        # Create a base64 string that would decode to more than 1MB
        large_content = "A" * (2 * 1024 * 1024)  # ~1.5MB when decoded

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = await extractor.extract_content(
                filename="large.pdf",
                content_base64=large_content
            )

            assert result.success is False
            assert "File too large" in result.error

    @pytest.mark.asyncio
    async def test_extract_content_http_error(self):
        """extract_content should handle HTTP errors gracefully."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 500

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert result.success is False
                assert "status 500" in result.error

    @pytest.mark.asyncio
    async def test_extract_content_timeout(self):
        """extract_content should handle timeout gracefully."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    timeout_seconds=5
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert result.success is False
                assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_extract_content_connection_error(self):
        """extract_content should handle connection errors gracefully."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(
                    side_effect=httpx.RequestError("Connection refused")
                )
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert result.success is False
                assert "Failed to connect" in result.error

    @pytest.mark.asyncio
    async def test_extract_content_preview_truncation(self):
        """extract_content should truncate preview for long content."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    response_field="text",
                    preview_chars=20
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        long_text = "A" * 100  # 100 characters
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "text": long_text
        }

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert result.success is True
                assert result.content == long_text
                assert result.preview == "A" * 20 + "..."
                assert len(result.preview) == 23  # 20 + "..."


class TestExtractionResult:
    """Test ExtractionResult dataclass."""

    def test_extraction_result_success(self):
        """ExtractionResult should store successful extraction data."""
        result = ExtractionResult(
            success=True,
            content="Extracted text",
            preview="Extracted...",
            metadata={"pages": 3}
        )

        assert result.success is True
        assert result.content == "Extracted text"
        assert result.preview == "Extracted..."
        assert result.metadata == {"pages": 3}
        assert result.error is None

    def test_extraction_result_failure(self):
        """ExtractionResult should store failure information."""
        result = ExtractionResult(
            success=False,
            error="Connection refused"
        )

        assert result.success is False
        assert result.error == "Connection refused"
        assert result.content is None
        assert result.preview is None
        assert result.metadata is None


class TestFileExtractorApiKeyAndHeaders:
    """Test FileExtractorConfig api_key and headers functionality."""

    def test_file_extractor_config_with_api_key(self):
        """FileExtractorConfig should accept api_key field."""
        config = FileExtractorConfig(
            url="http://localhost:8010/extract",
            api_key="sk-test-key-123"
        )

        assert config.api_key == "sk-test-key-123"

    def test_file_extractor_config_with_headers(self):
        """FileExtractorConfig should accept headers field."""
        config = FileExtractorConfig(
            url="http://localhost:8010/extract",
            headers={"X-Client-ID": "client-123", "X-Custom-Header": "value"}
        )

        assert config.headers == {"X-Client-ID": "client-123", "X-Custom-Header": "value"}

    def test_file_extractor_config_api_key_and_headers_optional(self):
        """api_key and headers should be optional (None by default)."""
        config = FileExtractorConfig(url="http://localhost:8010/extract")

        assert config.api_key is None
        assert config.headers is None

    def test_file_extractor_env_var_resolution_api_key(self, monkeypatch):
        """ConfigManager should resolve ${ENV_VAR} in api_key."""
        from modules.config.config_manager import resolve_env_var

        monkeypatch.setenv("TEST_EXTRACTOR_API_KEY", "sk-resolved-key-456")

        # Test resolve_env_var directly
        resolved = resolve_env_var("${TEST_EXTRACTOR_API_KEY}")
        assert resolved == "sk-resolved-key-456"

    def test_file_extractor_env_var_resolution_headers(self, monkeypatch):
        """ConfigManager should resolve ${ENV_VAR} in header values."""
        from modules.config.config_manager import resolve_env_var

        monkeypatch.setenv("TEST_CLIENT_ID", "client-resolved-789")

        resolved = resolve_env_var("${TEST_CLIENT_ID}")
        assert resolved == "client-resolved-789"

    def test_file_extractor_env_var_optional_returns_none(self):
        """resolve_env_var with required=False should return None for missing vars."""
        from modules.config.config_manager import resolve_env_var

        # Missing env var with required=False should return None
        result = resolve_env_var("${MISSING_OPTIONAL_KEY}", required=False)
        assert result is None

    def test_file_extractor_env_var_required_raises(self):
        """resolve_env_var with required=True should raise for missing vars."""
        from modules.config.config_manager import resolve_env_var

        with pytest.raises(ValueError) as exc_info:
            resolve_env_var("${MISSING_REQUIRED_KEY}", required=True)

        assert "MISSING_REQUIRED_KEY" in str(exc_info.value)

    def test_file_extractor_literal_value_unchanged(self):
        """resolve_env_var should return literal values unchanged."""
        from modules.config.config_manager import resolve_env_var

        result = resolve_env_var("sk-literal-key")
        assert result == "sk-literal-key"

    @pytest.mark.asyncio
    async def test_extract_content_includes_api_key_header(self):
        """extract_content should include api_key as Authorization header."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    api_key="sk-test-api-key",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Extracted content"}

        captured_headers = {}

        async def capture_request(*args, **kwargs):
            nonlocal captured_headers
            captured_headers = kwargs.get("headers", {})
            return mock_response

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=capture_request)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert "Authorization" in captured_headers
                assert captured_headers["Authorization"] == "Bearer sk-test-api-key"

    @pytest.mark.asyncio
    async def test_extract_content_includes_custom_headers(self):
        """extract_content should include custom headers from config."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    headers={"X-Client-ID": "my-client", "X-Request-Source": "atlas-ui"},
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Extracted content"}

        captured_headers = {}

        async def capture_request(*args, **kwargs):
            nonlocal captured_headers
            captured_headers = kwargs.get("headers", {})
            return mock_response

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=capture_request)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert captured_headers.get("X-Client-ID") == "my-client"
                assert captured_headers.get("X-Request-Source") == "atlas-ui"

    @pytest.mark.asyncio
    async def test_extract_content_no_headers_when_not_configured(self):
        """extract_content should pass None headers when not configured."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    response_field="text"
                    # No api_key or headers
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Extracted content"}

        captured_headers = "NOT_SET"

        async def capture_request(*args, **kwargs):
            nonlocal captured_headers
            captured_headers = kwargs.get("headers")
            return mock_response

        with patch('modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(side_effect=capture_request)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                assert captured_headers is None


class TestConfigManagerFileExtractors:
    """Test ConfigManager loading of file extractors config."""

    def test_config_manager_loads_file_extractors(self):
        """ConfigManager should load file extractors configuration."""
        cm = ConfigManager()
        config = cm.file_extractors_config

        assert config is not None
        assert isinstance(config, FileExtractorsConfig)

    def test_config_manager_caches_file_extractors(self):
        """ConfigManager should cache file extractors config."""
        cm = ConfigManager()

        config1 = cm.file_extractors_config
        config2 = cm.file_extractors_config

        assert config1 is config2

    def test_config_manager_returns_disabled_on_missing_file(self):
        """ConfigManager should return disabled config if file not found."""
        cm = ConfigManager()

        # Clear cached config
        cm._file_extractors_config = None

        # Mock _search_paths to return empty paths
        with patch.object(cm, '_load_file_with_error_handling', return_value=None):
            config = cm.file_extractors_config

            assert config.enabled is False
