"""File storage module for the chat backend.

This module provides:
- S3 storage client for file operations
- Mock S3 client for local development (no Docker required)
- File management utilities
- Content type detection and categorization
"""

from .manager import FileManager
from .s3_client import S3StorageClient

__all__ = [
    "S3StorageClient",
    "FileManager",
]
