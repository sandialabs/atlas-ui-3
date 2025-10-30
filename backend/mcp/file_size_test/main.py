#!/usr/bin/env python3
"""
File Size Test MCP Server using FastMCP.
Simple tool for testing file transfer by returning file size.
"""

import base64
import os
import logging
from typing import Any, Dict, Annotated

import requests
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP("File_Size_Test")


@mcp.tool
def process_file_demo(
    filename: Annotated[str, "The file to process (URL or base64)"],
    username: Annotated[str, "Username for auditing"] = None
) -> Dict[str, Any]:
    """
    Demo tool that processes a file and returns a new transformed file.

    This tool demonstrates the v2 MCP artifacts contract by:
    - Accepting a file input
    - Processing it (converting text to uppercase for demo)
    - Returning a new file as an artifact with proper v2 format
    - Including display hints for canvas viewing

    **v2 Artifacts Contract:**
    - Uses artifacts array with base64 content
    - Includes MIME types and metadata
    - Provides display hints for canvas behavior
    - Supports username injection for auditing

    **File Processing:**
    - For text files: converts content to uppercase
    - For binary files: demonstrates file modification capability
    - Preserves original file structure where possible

    **Return Format:**
    - results: Summary of operation
    - artifacts: Array containing the processed file
    - display: Canvas hints (open_canvas: true, primary_file, etc.)
    - meta_data: Additional processing details

    Args:
        filename: File reference (URL or base64 data) to process
        username: Injected user identity for auditing

    Returns:
        Dictionary with results, artifacts, and display hints per v2 contract
    """
    print(f"DEBUG: process_file_demo called with filename: {filename}")
    print(f"DEBUG: username: {username}")
    try:
        # Get the file content (reuse logic from get_file_size)
        is_url = (
            filename.startswith("http://") or
            filename.startswith("https://") or
            filename.startswith("/api/") or
            filename.startswith("/")
        )
        print(f"DEBUG: is_url determined as: {is_url}")

        if is_url:
            if filename.startswith("/"):
                backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
                url = f"{backend_url}{filename}"
            else:
                url = filename
            logger.info(f"Downloading file for processing: {url}")
            response = requests.get(url)
            response.raise_for_status()
            file_bytes = response.content
            original_filename = filename.split('/')[-1] or "processed_file.txt"
        else:
            # Assume base64
            logger.info("Decoding base64 for file processing")
            file_bytes = base64.b64decode(filename)
            original_filename = "processed_file.txt"

        print(f"DEBUG: Original file size: {len(file_bytes)} bytes")

        # Process the file (demo: convert text to uppercase)
        try:
            # Try to decode as text for processing
            original_text = file_bytes.decode('utf-8')
            processed_text = original_text.upper()
            processed_bytes = processed_text.encode('utf-8')
            processed_mime = "text/plain"
            description = "Processed text (converted to uppercase)"
        except UnicodeDecodeError:
            # If not text, do a simple binary modification (demo purpose)
            processed_bytes = file_bytes + b"\n[DEMO PROCESSED]"
            processed_mime = "application/octet-stream"
            description = "Processed binary file (demo modification)"

        # Create artifact
        processed_b64 = base64.b64encode(processed_bytes).decode('ascii')
        new_filename = f"processed_{original_filename}"

        # Create display hints
        display_hints = {
            "open_canvas": True,
            "primary_file": new_filename,
            "mode": "replace",
            "viewer_hint": "auto"
        }

        result = {
            "results": {
                "operation": "process_file_demo",
                "original_filename": original_filename,
                "processed_filename": new_filename,
                "original_size": len(file_bytes),
                "processed_size": len(processed_bytes),
                "processing_type": "text_uppercase" if 'original_text' in locals() else "binary_demo",
                "status": "success"
            },
            "meta_data": {
                "is_error": False,
                "processed_by": "process_file_demo_v2",
                "username": username,
                "mime_type": processed_mime
            },
            "artifacts": [
                {
                    "name": new_filename,
                    "b64": processed_b64,
                    "mime": processed_mime,
                    "size": len(processed_bytes),
                    "description": description,
                    "viewer": "auto"
                }
            ],
            "display": display_hints
        }
        print(f"DEBUG: About to return processed file result: {result['results']}")
        return result

    except Exception as e:
        print(f"DEBUG: Exception in process_file_demo: {str(e)}")
        import traceback
        traceback.print_exc()
        error_result = {
            "results": {
                "operation": "process_file_demo",
                "error": f"File processing failed: {str(e)}",
                "filename": filename
            },
            "meta_data": {
                "is_error": True,
                "error_type": type(e).__name__,
                "username": username
            }
        }
        return error_result


@mcp.tool
def get_file_size(
    filename: Annotated[str, "The file to check (URL or base64)"]
) -> Dict[str, Any]:
    """
    Test file transfer by returning the size of the transferred file.

    This simple tool is designed for testing file transfer functionality
    between frontend and backend. It accepts a file and returns its size in bytes.

    **File Input Support:**
    - URL-based files (http://, https://, or /api/ paths)
    - Base64-encoded file data
    - Automatic backend URL construction for relative paths

    **Return Information:**
    - File size in bytes
    - File size in human-readable format (KB, MB)
    - Original filename or URL

    **Use Cases:**
    - Testing file upload/download workflows
    - Validating file transfer infrastructure
    - Debugging file handling issues
    - Verifying file size limits

    Args:
        filename: File reference (URL or base64 data)

    Returns:
        Dictionary containing:
        - operation: "get_file_size"
        - filename: Original filename/URL
        - size_bytes: File size in bytes
        - size_human: Human-readable size (e.g., "1.5 MB")
        Or error message if file cannot be accessed
    """
    print(f"DEBUG: get_file_size called with filename: {filename}")
    print(f"DEBUG: filename type: {type(filename)}, length: {len(filename) if filename else 0}")
    try:
        # Check if filename is a URL (absolute or relative)
        is_url = (
            filename.startswith("http://") or
            filename.startswith("https://") or
            filename.startswith("/api/") or
            filename.startswith("/")
        )
        print(f"DEBUG: is_url determined as: {is_url}")

        if is_url:
            # Convert relative URLs to absolute URLs
            if filename.startswith("/"):
                backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
                url = f"{backend_url}{filename}"
                print(f"DEBUG: Constructing URL from relative path: {filename} -> {url}")
            else:
                url = filename
                print(f"DEBUG: Using absolute URL: {url}")

            print(f"DEBUG: About to download from URL: {url}")
            logger.info(f"Downloading file from URL: {url}")
            response = requests.get(url)
            print(f"DEBUG: HTTP response status: {response.status_code}")
            response.raise_for_status()
            file_bytes = response.content
            print(f"DEBUG: Successfully downloaded file content, length: {len(file_bytes)} bytes")
        else:
            # Assume it's base64-encoded data
            print(f"DEBUG: Treating input as base64 data, attempting to decode")
            logger.info("Decoding base64 file data")
            file_bytes = base64.b64decode(filename)
            print(f"DEBUG: Successfully decoded base64 data, length: {len(file_bytes)} bytes")

        # Calculate file size
        size_bytes = len(file_bytes)
        size_human = _format_size(size_bytes)
        print(f"DEBUG: Calculated file size: {size_bytes} bytes ({size_human})")

        result = {
            "results": {
                "operation": "get_file_size",
                "filename": filename,
                "size_bytes": size_bytes,
                "size_human": size_human,
                "status": "success"
            },
            "meta_data": {
                "is_error": False,
                "transfer_method": "url" if is_url else "base64"
            }
        }
        print(f"DEBUG: About to return success result: {result}")
        return result

    except Exception as e:
        print(f"DEBUG: Exception occurred while processing file: {str(e)}")
        print(f"DEBUG: Exception type: {type(e).__name__}")
        print(f"DEBUG: Filename that caused error: {filename}")
        import traceback
        print("DEBUG: Full traceback:")
        traceback.print_exc()
        error_result = {
            "results": {
                "operation": "get_file_size",
                "error": f"File size check failed: {str(e)}",
                "filename": filename
            },
            "meta_data": {
                "is_error": True,
                "error_type": type(e).__name__
            }
        }
        print(f"DEBUG: About to return error result: {error_result}")
        return error_result


def _format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


if __name__ == "__main__":
    mcp.run()
