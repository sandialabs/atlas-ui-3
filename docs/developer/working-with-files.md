# Working with Files (S3 Storage)

When a user uploads a file, it is stored in an S3-compatible object store. As a tool developer, you do not need to interact with S3 directly. The backend provides a secure mechanism for your tools to access file content.

## The File Access Workflow

1.  **Define Your Tool**: Create a tool that accepts a `filename` (or `file_names`) argument.
    ```python
    @mcp.tool
    def process_file(filename: str) -> Dict[str, Any]:
        # ...
    ```
2.  **Receive a Secure URL**: When the LLM calls your tool, the backend intercepts the call. It replaces the simple filename (e.g., `my_document.pdf`) with a full, temporary URL that points back to the Atlas UI API (e.g., `http://localhost:8000/api/files/download/...`). This URL contains a short-lived security token.
3.  **Fetch the File Content**: Your tool should then make a standard HTTP `GET` request to this URL to download the file's content.

## Example Tool for File Processing

```python
import httpx
from fastmcp import FastMCP
from typing import Dict, Any

mcp = FastMCP(name="FileProcessor")

@mcp.tool
def get_file_size(filename: str) -> Dict[str, Any]:
    """
    Accepts a file URL, downloads the file, and returns its size.
    """
    try:
        with httpx.stream("GET", filename, timeout=30) as response:
            response.raise_for_status()
            content = response.read()
            file_size = len(content)
            
            return {
                "results": {
                    "file_size_bytes": file_size
                }
            }
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP error fetching file: {e.response.status_code}"}
    except Exception as e:
        return {"error": f"Failed to process file: {str(e)}"}

if __name__ == "__main__":
    mcp.run()
```

This architecture ensures that your tool does not need to handle any S3 credentials, making the system more secure and easier to develop for.
