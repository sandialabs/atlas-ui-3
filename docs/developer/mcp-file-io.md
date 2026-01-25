# MCP Tool File I/O Guide

Last updated: 2026-01-25

This guide explains how to accept files as input and return files as output from MCP tools using FastMCP. It is self-contained and can be used independently.

## Overview

MCP tools can:
1. **Accept files** - Receive a URL to fetch file content from
2. **Return files** - Output binary files (images, documents, etc.) as base64-encoded artifacts

## Accepting Files as Input

When users upload files and the LLM passes them to your tool, the backend rewrites the filename to a secure download URL. This URL may be:

- **Relative path**: `/api/files/download/abc123?token=xyz` (default behavior)
- **Absolute URL**: `http://localhost:8000/api/files/download/abc123?token=xyz` (when `BACKEND_PUBLIC_URL` is configured)

Your tool should handle both cases.

### Step 1: Define a `filename` Parameter

```python
from fastmcp import FastMCP
from typing import Dict, Any
import httpx

mcp = FastMCP(name="MyFileProcessor")

@mcp.tool
def process_document(filename: str) -> Dict[str, Any]:
    """
    Process an uploaded document.

    The filename parameter receives a download URL, not a raw filename.
    It may be a relative path or an absolute URL depending on backend config.
    """
    # filename could be either:
    # - Relative: /api/files/download/abc123?token=xyz
    # - Absolute: http://localhost:8000/api/files/download/abc123?token=xyz
    pass
```

### Step 2: Fetch the File Content

Use an HTTP client to download the file. Since the backend may return either relative or absolute URLs, normalize relative paths first:

```python
import os
import httpx
from urllib.parse import urljoin

# Get the backend base URL from environment (defaults to localhost for development)
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000")

def normalize_file_url(filename: str) -> str:
    """Convert relative paths to absolute URLs."""
    if filename.startswith("/"):
        return urljoin(BACKEND_PUBLIC_URL, filename)
    return filename

@mcp.tool
def process_document(filename: str) -> Dict[str, Any]:
    """Process an uploaded document."""
    try:
        file_url = normalize_file_url(filename)
        response = httpx.get(file_url, timeout=30)
        response.raise_for_status()
        file_bytes = response.content

        # Now process file_bytes...
        return {"results": {"size": len(file_bytes)}}

    except httpx.HTTPStatusError as e:
        return {"results": {"error": f"Failed to fetch file: {e.response.status_code}"}}
```

### Multiple Files

For multiple files, use `file_names: List[str]`:

```python
from typing import List

@mcp.tool
def merge_documents(file_names: List[str]) -> Dict[str, Any]:
    """Merge multiple uploaded documents."""
    all_content = []
    for url in file_names:
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        all_content.append(response.content)
    # Process all_content...
```

### Base64 Fallback

Some integrations may provide base64-encoded file content directly. Handle both cases:

```python
import base64

@mcp.tool
def process_image(
    filename: str,
    file_data_base64: str = ""
) -> Dict[str, Any]:
    """Process an image from URL or base64 data."""

    if file_data_base64:
        # Direct base64 content provided
        image_bytes = base64.b64decode(file_data_base64)
    else:
        # Fetch from URL
        response = httpx.get(filename, timeout=30)
        response.raise_for_status()
        image_bytes = response.content

    # Process image_bytes...
```

## Returning Files as Output

Return files using the `artifacts` array with base64-encoded content.

### Basic Structure

```python
import base64

@mcp.tool
def generate_report() -> Dict[str, Any]:
    """Generate a report file."""

    # Create your content
    html_content = "<html><body><h1>Report</h1></body></html>"

    # Encode to base64
    b64_content = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "message": "Report generated successfully"
        },
        "artifacts": [
            {
                "name": "report.html",
                "b64": b64_content,
                "mime": "text/html"
            }
        ]
    }
```

### Artifact Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Filename with extension (e.g., `output.pdf`) |
| `b64` | Yes | Base64-encoded file content |
| `mime` | Yes | MIME type (e.g., `application/pdf`, `image/png`) |

### Common MIME Types

| Extension | MIME Type |
|-----------|-----------|
| `.html` | `text/html` |
| `.pdf` | `application/pdf` |
| `.png` | `image/png` |
| `.jpg` | `image/jpeg` |
| `.csv` | `text/csv` |
| `.json` | `application/json` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| `.zip` | `application/zip` |

### Returning Multiple Files

```python
@mcp.tool
def generate_files() -> Dict[str, Any]:
    """Generate multiple output files."""

    html_b64 = base64.b64encode(b"<html>...</html>").decode('utf-8')
    csv_b64 = base64.b64encode(b"col1,col2\n1,2").decode('utf-8')

    return {
        "results": {"message": "Generated 2 files"},
        "artifacts": [
            {"name": "report.html", "b64": html_b64, "mime": "text/html"},
            {"name": "data.csv", "b64": csv_b64, "mime": "text/csv"}
        ]
    }
```

### Display Hints

Control how the UI displays artifacts:

```python
return {
    "results": {"message": "Done"},
    "artifacts": [
        {"name": "output.html", "b64": b64_content, "mime": "text/html"}
    ],
    "display": {
        "open_canvas": True,        # Auto-open the canvas panel
        "primary_file": "output.html"  # Which file to show first
    }
}
```

| Field | Status | Description |
|-------|--------|-------------|
| `open_canvas` | Supported | Auto-open the canvas panel when artifacts are returned |
| `primary_file` | Supported | Which file to display first in the canvas |

## Complete Example: File Converter

This example accepts an input file and returns a converted output file:

```python
from fastmcp import FastMCP
from typing import Dict, Any, Optional
import base64
import httpx
import tempfile
import os

mcp = FastMCP(name="FileConverter")

@mcp.tool
def markdown_to_html(
    markdown_content: str,
    filename: Optional[str] = None,
    file_data_base64: Optional[str] = None,
    output_name: str = "output"
) -> Dict[str, Any]:
    """
    Convert markdown to HTML.

    Can accept markdown as:
    - Direct text in markdown_content
    - A file URL in filename
    - Base64 content in file_data_base64
    """
    # Get the markdown content
    if file_data_base64:
        md_text = base64.b64decode(file_data_base64).decode('utf-8')
    elif filename and filename.startswith("http"):
        response = httpx.get(filename, timeout=30)
        response.raise_for_status()
        md_text = response.content.decode('utf-8')
    else:
        md_text = markdown_content

    if not md_text:
        return {"results": {"error": "No markdown content provided"}}

    # Convert to HTML (simplified)
    html_content = f"""<!DOCTYPE html>
<html>
<head><title>{output_name}</title></head>
<body>
<pre>{md_text}</pre>
</body>
</html>"""

    # Encode output
    html_b64 = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "message": f"Converted markdown to HTML",
            "output_file": f"{output_name}.html"
        },
        "artifacts": [
            {
                "name": f"{output_name}.html",
                "b64": html_b64,
                "mime": "text/html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": f"{output_name}.html"
        }
    }

if __name__ == "__main__":
    mcp.run()
```

## Working with Binary Files

For binary formats (images, PDFs, Office docs), work with bytes directly:

```python
from pptx import Presentation
import io

@mcp.tool
def create_presentation(title: str) -> Dict[str, Any]:
    """Create a PowerPoint presentation."""

    # Create presentation
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title

    # Save to bytes buffer
    buffer = io.BytesIO()
    prs.save(buffer)
    buffer.seek(0)
    pptx_bytes = buffer.read()

    # Encode to base64
    pptx_b64 = base64.b64encode(pptx_bytes).decode('utf-8')

    return {
        "results": {"message": f"Created presentation: {title}"},
        "artifacts": [
            {
                "name": f"{title}.pptx",
                "b64": pptx_b64,
                "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": f"{title}.pptx"
        }
    }
```

## Using Temp Files

For libraries that require file paths:

```python
import tempfile

@mcp.tool
def process_and_output() -> Dict[str, Any]:
    """Process data and create output file."""

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "output.xlsx")

        # Library writes to file path
        create_excel_file(output_path)

        # Read back as bytes
        with open(output_path, "rb") as f:
            file_bytes = f.read()

        # Encode and return
        b64 = base64.b64encode(file_bytes).decode('utf-8')

        return {
            "results": {"message": "Created Excel file"},
            "artifacts": [
                {"name": "output.xlsx", "b64": b64, "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
            ]
        }
    # Temp directory auto-cleaned up
```

## Security Considerations

1. **Validate input URLs** - Only fetch from expected URL patterns
2. **Sanitize filenames** - Remove path traversal characters from output names
3. **Set timeouts** - Always use timeouts when fetching remote files
4. **Limit file sizes** - Check content length before processing large files

```python
import os
import re
from urllib.parse import urlparse

BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000")

def _sanitize_filename(name: str, max_length: int = 50) -> str:
    """Remove unsafe characters from filename."""
    cleaned = re.sub(r'[^\w\-.]', '', name)
    return cleaned[:max_length] if cleaned else "output"

def _is_safe_url(url: str) -> bool:
    """Check if URL is from expected backend."""
    # Allow relative download URLs
    if url.startswith("/api/files/download/"):
        return True

    # For absolute URLs, validate against configured backend
    parsed = urlparse(url)
    if not parsed.netloc:
        return False

    public = urlparse(BACKEND_PUBLIC_URL)
    return (
        parsed.scheme == public.scheme
        and parsed.netloc == public.netloc
        and parsed.path.startswith("/api/files/download/")
    )
```

## Summary

| Direction | Method | Key Points |
|-----------|--------|------------|
| **Input** | `filename: str` parameter | Backend rewrites to download URL (relative or absolute); normalize and fetch with HTTP client |
| **Output** | `artifacts` array | Base64-encode content; include name, b64, mime fields |

The MCP framework handles all the complexity of file storage and retrieval. Your tool just needs to normalize and fetch URLs, then return base64-encoded artifacts.
