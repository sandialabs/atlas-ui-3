"""
File content extraction client for calling HTTP-based extraction services.

This module provides a generic interface for extracting content from files
(PDFs, images, etc.) via configurable HTTP endpoints.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from modules.config.config_manager import (
    FileExtractorConfig,
    FileExtractorsConfig,
    get_app_settings,
    get_file_extractors_config,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of a content extraction attempt."""
    success: bool
    content: Optional[str] = None
    preview: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[dict] = None


class FileContentExtractor:
    """
    Client for extracting content from files using configured HTTP services.

    Supports extension-based and MIME-type-based extractor lookup with
    configurable preview length truncation.
    """

    def __init__(self, config: Optional[FileExtractorsConfig] = None):
        """
        Initialize the extractor with optional config override.

        Args:
            config: Optional config override. If None, loads from config manager.
        """
        self._config = config

    @property
    def config(self) -> FileExtractorsConfig:
        """Get the extractors configuration (lazy loaded)."""
        if self._config is None:
            self._config = get_file_extractors_config()
        return self._config

    def is_enabled(self) -> bool:
        """Check if file content extraction is enabled globally."""
        app_settings = get_app_settings()
        return (
            app_settings.feature_file_content_extraction_enabled
            and self.config.enabled
        )

    def get_default_behavior(self) -> str:
        """Get the default extraction behavior ('extract' or 'attach_only')."""
        return self.config.default_behavior

    def get_extractor_for_file(
        self, filename: str, mime_type: Optional[str] = None
    ) -> Optional[FileExtractorConfig]:
        """
        Find the appropriate extractor for a file based on extension or MIME type.

        Args:
            filename: The filename to look up
            mime_type: Optional MIME type for fallback lookup

        Returns:
            FileExtractorConfig if found and enabled, None otherwise
        """
        if not self.is_enabled():
            return None

        # Try extension-based lookup first
        ext = Path(filename).suffix.lower()
        extractor_name = self.config.extension_mapping.get(ext)

        # Fall back to MIME type lookup
        if not extractor_name and mime_type:
            extractor_name = self.config.mime_mapping.get(mime_type)

        if not extractor_name:
            logger.debug(f"No extractor mapping for file: {filename} (mime: {mime_type})")
            return None

        extractor = self.config.extractors.get(extractor_name)
        if not extractor:
            logger.warning(f"Extractor '{extractor_name}' not found in config")
            return None

        if not extractor.enabled:
            logger.debug(f"Extractor '{extractor_name}' is disabled")
            return None

        return extractor

    def can_extract(self, filename: str, mime_type: Optional[str] = None) -> bool:
        """
        Check if content extraction is possible for a given file.

        Args:
            filename: The filename to check
            mime_type: Optional MIME type

        Returns:
            True if an enabled extractor is available for this file type
        """
        return self.get_extractor_for_file(filename, mime_type) is not None

    def get_supported_extensions(self) -> list[str]:
        """Get list of file extensions that have extraction support."""
        if not self.is_enabled():
            return []

        supported = []
        for ext, extractor_name in self.config.extension_mapping.items():
            extractor = self.config.extractors.get(extractor_name)
            if extractor and extractor.enabled:
                supported.append(ext)
        return supported

    async def extract_content(
        self,
        filename: str,
        content_base64: str,
        mime_type: Optional[str] = None,
    ) -> ExtractionResult:
        """
        Extract content from a file using the appropriate HTTP extractor service.

        Args:
            filename: The name of the file
            content_base64: Base64-encoded file content
            mime_type: Optional MIME type of the file

        Returns:
            ExtractionResult with extracted content or error information
        """
        extractor = self.get_extractor_for_file(filename, mime_type)
        if not extractor:
            return ExtractionResult(
                success=False,
                error=f"No extractor available for file: {filename}"
            )

        # Check file size limit
        content_size_mb = len(content_base64) * 3 / 4 / (1024 * 1024)  # Approximate decoded size
        if content_size_mb > extractor.max_file_size_mb:
            return ExtractionResult(
                success=False,
                error=f"File too large: {content_size_mb:.1f}MB exceeds limit of {extractor.max_file_size_mb}MB"
            )

        try:
            # Build request payload
            payload = {
                "content": content_base64,
                "filename": filename,
                "options": {
                    "preview_chars": extractor.preview_chars,
                }
            }

            # Build request headers
            request_headers = {}

            # Add API key as Authorization header if configured
            if extractor.api_key:
                request_headers["Authorization"] = f"Bearer {extractor.api_key}"

            # Add any custom headers from config
            if extractor.headers:
                request_headers.update(extractor.headers)

            async with httpx.AsyncClient(timeout=extractor.timeout_seconds) as client:
                response = await client.request(
                    method=extractor.method,
                    url=extractor.url,
                    json=payload,
                    headers=request_headers if request_headers else None,
                )

                if response.status_code != 200:
                    logger.warning(
                        f"Extractor returned status {response.status_code} for {filename}"
                    )
                    return ExtractionResult(
                        success=False,
                        error=f"Extractor service returned status {response.status_code}"
                    )

                result_data = response.json()

                # Check for success flag if present
                if "success" in result_data and not result_data["success"]:
                    return ExtractionResult(
                        success=False,
                        error=result_data.get("error", "Extraction failed")
                    )

                # Extract the content field
                extracted_text = result_data.get(extractor.response_field)
                if extracted_text is None:
                    return ExtractionResult(
                        success=False,
                        error=f"Response missing expected field: {extractor.response_field}"
                    )

                # Generate preview if content is longer than preview_chars
                preview = None
                if extractor.preview_chars and len(extracted_text) > extractor.preview_chars:
                    preview = extracted_text[:extractor.preview_chars] + "..."
                else:
                    preview = extracted_text

                return ExtractionResult(
                    success=True,
                    content=extracted_text,
                    preview=preview,
                    metadata=result_data.get("metadata")
                )

        except httpx.TimeoutException:
            logger.warning(f"Extraction timeout for {filename} after {extractor.timeout_seconds}s")
            return ExtractionResult(
                success=False,
                error=f"Extraction timed out after {extractor.timeout_seconds} seconds"
            )
        except httpx.RequestError as e:
            logger.warning(f"Extraction request failed for {filename}: {e}")
            return ExtractionResult(
                success=False,
                error=f"Failed to connect to extractor service: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error extracting content from {filename}: {e}", exc_info=True)
            return ExtractionResult(
                success=False,
                error=f"Unexpected extraction error: {str(e)}"
            )


# Module-level instance for convenience
_extractor_instance: Optional[FileContentExtractor] = None


def get_content_extractor() -> FileContentExtractor:
    """Get the shared file content extractor instance."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = FileContentExtractor()
    return _extractor_instance
