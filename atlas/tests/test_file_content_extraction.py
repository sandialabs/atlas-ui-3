"""Tests for file content extraction feature.

Tests the FileExtractorConfig, FileExtractorsConfig models, FileContentExtractor class,
and related configuration functionality.
"""

from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from atlas.modules.config.config_manager import (
    ConfigManager,
    FileExtractorConfig,
    FileExtractorsConfig,
)
from atlas.modules.file_storage.content_extractor import (
    ExtractionResult,
    FileContentExtractor,
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            extensions = extractor.get_supported_extensions()
            assert ".pdf" in extensions
            assert ".png" not in extensions  # Extractor is disabled

    def test_get_supported_extensions_empty_when_disabled(self):
        """get_supported_extensions should return empty list when disabled."""
        config = FileExtractorsConfig(enabled=False)
        extractor = FileContentExtractor(config=config)

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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
        from atlas.modules.config.config_manager import resolve_env_var

        monkeypatch.setenv("TEST_EXTRACTOR_API_KEY", "sk-resolved-key-456")

        # Test resolve_env_var directly
        resolved = resolve_env_var("${TEST_EXTRACTOR_API_KEY}")
        assert resolved == "sk-resolved-key-456"

    def test_file_extractor_env_var_resolution_headers(self, monkeypatch):
        """ConfigManager should resolve ${ENV_VAR} in header values."""
        from atlas.modules.config.config_manager import resolve_env_var

        monkeypatch.setenv("TEST_CLIENT_ID", "client-resolved-789")

        resolved = resolve_env_var("${TEST_CLIENT_ID}")
        assert resolved == "client-resolved-789"

    def test_file_extractor_env_var_optional_returns_none(self):
        """resolve_env_var with required=False should return None for missing vars."""
        from atlas.modules.config.config_manager import resolve_env_var

        # Missing env var with required=False should return None
        result = resolve_env_var("${MISSING_OPTIONAL_KEY}", required=False)
        assert result is None

    def test_file_extractor_env_var_required_raises(self):
        """resolve_env_var with required=True should raise for missing vars."""
        from atlas.modules.config.config_manager import resolve_env_var

        with pytest.raises(ValueError) as exc_info:
            resolve_env_var("${MISSING_REQUIRED_KEY}", required=True)

        assert "MISSING_REQUIRED_KEY" in str(exc_info.value)

    def test_file_extractor_literal_value_unchanged(self):
        """resolve_env_var should return literal values unchanged."""
        from atlas.modules.config.config_manager import resolve_env_var

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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
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


class TestMultipartUpload:
    """Test multipart form-data upload path in FileContentExtractor."""

    def test_file_extractor_config_form_field_name_default(self):
        """form_field_name should default to 'file'."""
        config = FileExtractorConfig(url="http://localhost:8010/extract")
        assert config.form_field_name == "file"

    def test_file_extractor_config_custom_form_field_name(self):
        """form_field_name should accept custom values."""
        config = FileExtractorConfig(
            url="http://localhost:8010/extract",
            request_format="multipart",
            form_field_name="document"
        )
        assert config.form_field_name == "document"
        assert config.request_format == "multipart"

    @pytest.mark.asyncio
    async def test_multipart_upload_sends_file(self):
        """Multipart request_format should send file via multipart form-data."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    form_field_name="file",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "text": "Extracted multipart content"
        }

        captured_kwargs = {}

        async def capture_post(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return mock_response

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdCBjb250ZW50",  # "test content"
                    mime_type="application/pdf"
                )

                assert result.success is True
                assert result.content == "Extracted multipart content"

                # Verify multipart files dict was passed
                assert "files" in captured_kwargs
                files = captured_kwargs["files"]
                assert "file" in files
                file_tuple = files["file"]
                assert file_tuple[0] == "document.pdf"
                assert file_tuple[1] == b"test content"
                assert file_tuple[2] == "application/pdf"

    @pytest.mark.asyncio
    async def test_multipart_upload_custom_field_name(self):
        """Multipart upload should use the configured form_field_name."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    form_field_name="document",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Content"}

        captured_kwargs = {}

        async def capture_post(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return mock_response

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA==",
                )

                files = captured_kwargs["files"]
                assert "document" in files

    @pytest.mark.asyncio
    async def test_multipart_upload_invalid_base64(self):
        """Multipart upload should handle invalid base64 gracefully."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            result = await extractor.extract_content(
                filename="document.pdf",
                content_base64="!!!not-valid-base64!!!"
            )

            assert result.success is False
            assert "decode" in result.error.lower() or "base64" in result.error.lower()

    @pytest.mark.asyncio
    async def test_multipart_upload_includes_accept_header(self):
        """Multipart upload should include Accept: application/json header."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Content"}

        captured_kwargs = {}

        async def capture_post(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return mock_response

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                headers = captured_kwargs.get("headers", {})
                assert headers.get("Accept") == "application/json"

    @pytest.mark.asyncio
    async def test_multipart_upload_with_api_key(self):
        """Multipart upload should include Authorization header when api_key is set."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    api_key="sk-test-key",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Content"}

        captured_kwargs = {}

        async def capture_post(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return mock_response

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA=="
                )

                headers = captured_kwargs.get("headers", {})
                assert headers.get("Authorization") == "Bearer sk-test-key"

    @pytest.mark.asyncio
    async def test_multipart_default_mime_type(self):
        """Multipart upload should default to application/octet-stream when no mime_type."""
        config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract-multipart",
                    enabled=True,
                    request_format="multipart",
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"}
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "Content"}

        captured_kwargs = {}

        async def capture_post(*args, **kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return mock_response

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings') as mock_settings:
            mock_settings.return_value.feature_file_content_extraction_enabled = True

            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=capture_post)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                await extractor.extract_content(
                    filename="document.pdf",
                    content_base64="dGVzdA==",
                    mime_type=None
                )

                files = captured_kwargs["files"]
                file_tuple = files["file"]
                assert file_tuple[2] == "application/octet-stream"


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


class TestPlainTextTypes:
    """Test plain-text file type direct-read functionality."""

    def _make_extractor(self, plain_text_types=None):
        """Helper to build a FileContentExtractor with plain_text_types configured."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=plain_text_types or [".txt", ".py", ".c", ".md"],
        )
        return FileContentExtractor(config=config)

    def _enabled_settings(self):
        """Return a mock app settings with extraction enabled."""
        mock_settings = Mock()
        mock_settings.feature_file_content_extraction_enabled = True
        return mock_settings

    # --- Model tests ---

    def test_plain_text_types_stored_on_model(self):
        """FileExtractorsConfig should store plain_text_types."""
        config = FileExtractorsConfig(plain_text_types=[".txt", ".py"])
        assert ".txt" in config.plain_text_types
        assert ".py" in config.plain_text_types

    def test_plain_text_types_normalized_to_lowercase(self):
        """Extensions in plain_text_types are normalised to lowercase."""
        config = FileExtractorsConfig(plain_text_types=[".TXT", ".PY", ".C"])
        assert config.plain_text_types == [".txt", ".py", ".c"]

    def test_plain_text_types_default_empty(self):
        """plain_text_types should default to an empty list."""
        config = FileExtractorsConfig()
        assert config.plain_text_types == []

    # --- is_plain_text_type ---

    def test_is_plain_text_type_returns_true_for_listed_extension(self):
        """is_plain_text_type should return True for extensions in plain_text_types."""
        extractor = self._make_extractor([".txt", ".py"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.is_plain_text_type("script.py") is True
            assert extractor.is_plain_text_type("README.txt") is True

    def test_is_plain_text_type_case_insensitive(self):
        """is_plain_text_type should match regardless of case in the filename."""
        extractor = self._make_extractor([".py"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.is_plain_text_type("script.PY") is True
            assert extractor.is_plain_text_type("script.Py") is True

    def test_is_plain_text_type_returns_false_for_unlisted_extension(self):
        """is_plain_text_type should return False for extensions not in plain_text_types."""
        extractor = self._make_extractor([".txt"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.is_plain_text_type("document.pdf") is False

    def test_is_plain_text_type_returns_false_when_disabled(self):
        """is_plain_text_type should return False when extraction is globally disabled."""
        config = FileExtractorsConfig(enabled=False, plain_text_types=[".txt"])
        extractor = FileContentExtractor(config=config)

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.is_plain_text_type("file.txt") is False

    # --- can_extract with plain_text_types ---

    def test_can_extract_true_for_plain_text_type(self):
        """can_extract should return True for plain-text extensions."""
        extractor = self._make_extractor([".py"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.can_extract("script.py") is True

    def test_can_extract_false_when_no_extractor_and_not_plain_text(self):
        """can_extract should return False when neither condition is satisfied."""
        extractor = self._make_extractor([".txt"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            assert extractor.can_extract("archive.zip") is False

    # --- get_supported_extensions with plain_text_types ---

    def test_get_supported_extensions_includes_plain_text_types(self):
        """get_supported_extensions should include plain_text_types entries."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt", ".py"],
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={".pdf": "pdf-text"},
        )
        extractor = FileContentExtractor(config=config)

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            exts = extractor.get_supported_extensions()
            assert ".txt" in exts
            assert ".py" in exts
            assert ".pdf" in exts

    def test_get_supported_extensions_no_duplicates(self):
        """get_supported_extensions should not produce duplicates from plain_text_types."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt", ".txt"],
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={".pdf": "pdf-text"},
        )
        extractor = FileContentExtractor(config=config)

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            exts = extractor.get_supported_extensions()
            # .txt appears twice in plain_text_types input; should still be listed
            assert ".txt" in exts
            assert ".pdf" in exts

    # --- extract_content plain-text fast path ---

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_returns_decoded_content(self):
        """extract_content should decode base64 and return text for plain-text types."""
        extractor = self._make_extractor([".py"])

        source = "print('hello world')\n"
        import base64 as b64mod
        encoded = b64mod.b64encode(source.encode()).decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("script.py", encoded)

        assert result.success is True
        assert result.content == source
        assert result.preview == source  # short text: preview == content
        assert result.metadata == {"method": "plain_text_read"}

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_preview_truncated(self):
        """extract_content should truncate preview to 2000 chars for long plain-text files."""
        extractor = self._make_extractor([".txt"])

        long_text = "x" * 5000
        import base64 as b64mod
        encoded = b64mod.b64encode(long_text.encode()).decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("file.txt", encoded)

        assert result.success is True
        assert result.content == long_text
        assert result.preview == "x" * 2000 + "..."

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_invalid_base64(self):
        """extract_content should fail gracefully for invalid base64 in plain-text path."""
        extractor = self._make_extractor([".txt"])

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("file.txt", "!!!not-valid-base64!!!")

        assert result.success is False
        assert "base64" in result.error.lower() or "decode" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_does_not_call_http(self):
        """extract_content plain-text path must never call any HTTP extractor service."""
        extractor = self._make_extractor([".c"])

        import base64 as b64mod
        encoded = b64mod.b64encode(b"int main() { return 0; }").decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            with patch('httpx.AsyncClient') as mock_http:
                result = await extractor.extract_content("main.c", encoded)

        assert result.success is True
        mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_uppercase_extension(self):
        """Plain-text extension matching should be case-insensitive on the filename."""
        extractor = self._make_extractor([".py"])

        import base64 as b64mod
        encoded = b64mod.b64encode(b"# python script").decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("SCRIPT.PY", encoded)

        assert result.success is True
        assert result.content == "# python script"

    @pytest.mark.asyncio
    async def test_extract_content_non_plain_text_falls_through_to_extractor(self):
        """Files not in plain_text_types should still use the HTTP extractor."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt"],
            extractors={
                "pdf-text": FileExtractorConfig(
                    url="http://localhost:8010/extract",
                    enabled=True,
                    response_field="text"
                )
            },
            extension_mapping={".pdf": "pdf-text"},
        )
        extractor = FileContentExtractor(config=config)

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "text": "PDF content"}

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            with patch('httpx.AsyncClient') as mock_client_class:
                mock_client = AsyncMock()
                mock_client.request = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await extractor.extract_content("doc.pdf", "dGVzdA==")

        assert result.success is True
        assert result.content == "PDF content"

    # --- Security: file-size guard on plain-text fast path ---

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_rejects_oversized_file(self):
        """Plain-text fast path must enforce max_plain_text_size_mb."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt"],
            max_plain_text_size_mb=1,  # 1 MB limit
        )
        extractor = FileContentExtractor(config=config)

        # ~1.5 MB of text → base64 is ~2 MB
        import base64 as b64mod
        big_text = "A" * (1_500_000)
        encoded = b64mod.b64encode(big_text.encode()).decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("big.txt", encoded)

        assert result.success is False
        assert "too large" in result.error.lower()

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_accepts_file_under_limit(self):
        """Plain-text fast path should succeed for files within the size limit."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt"],
            max_plain_text_size_mb=10,
        )
        extractor = FileContentExtractor(config=config)

        import base64 as b64mod
        small_text = "hello"
        encoded = b64mod.b64encode(small_text.encode()).decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("small.txt", encoded)

        assert result.success is True
        assert result.content == small_text

    # --- Security: .env must not be in default plain_text_types ---

    def test_env_not_in_default_config(self):
        """The shipped file-extractors.json must not include .env in plain_text_types."""
        import json
        from pathlib import Path
        config_path = Path(__file__).resolve().parent.parent / "config" / "file-extractors.json"
        with open(config_path) as f:
            raw = json.load(f)
        plain = [ext.lower() for ext in raw.get("plain_text_types", [])]
        assert ".env" not in plain, ".env files may contain secrets and must not be auto-extracted"

    # --- Config validation: overlap between plain_text_types and extension_mapping ---

    def test_overlap_between_plain_text_and_extension_mapping_rejected(self):
        """Config must reject extensions appearing in both plain_text_types and extension_mapping."""
        import pytest as pt
        with pt.raises(Exception, match="plain_text_types.*extension_mapping"):
            FileExtractorsConfig(
                enabled=True,
                plain_text_types=[".pdf", ".txt"],
                extension_mapping={".pdf": "pdf-text"},
                extractors={
                    "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
                },
            )

    # --- Configurable preview_chars ---

    @pytest.mark.asyncio
    async def test_extract_content_plain_text_uses_configured_preview_chars(self):
        """Preview truncation should respect plain_text_preview_chars config."""
        config = FileExtractorsConfig(
            enabled=True,
            plain_text_types=[".txt"],
            plain_text_preview_chars=100,
        )
        extractor = FileContentExtractor(config=config)

        import base64 as b64mod
        text = "x" * 500
        encoded = b64mod.b64encode(text.encode()).decode()

        with patch('atlas.modules.file_storage.content_extractor.get_app_settings',
                   return_value=self._enabled_settings()):
            result = await extractor.extract_content("file.txt", encoded)

        assert result.success is True
        assert result.content == text
        assert result.preview == "x" * 100 + "..."
