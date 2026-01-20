# Working with Files (S3 Storage)

Last updated: 2026-01-19

When a user uploads a file, it is stored in an S3-compatible object store. As a tool developer, you do not need to interact with S3 directly. The backend provides a secure mechanism for your tools to access file content.

## The File Access Workflow

1.  **Define Your Tool**: Create a tool that accepts a `filename` (or `file_names`) argument.
    ```python
    @mcp.tool
    def process_file(filename: str) -> Dict[str, Any]:
        # ...
    ```
2.  **Receive a Secure URL**: When the LLM calls your tool, the backend intercepts the call. It replaces the simple filename (e.g., `my_document.pdf`) with a full, temporary URL that points back to the Atlas UI API (e.g., `http://localhost:8000/api/files/download/...` or `https://atlas.example.com/api/files/download/...`). This URL contains a short-lived security token.
3.  **Fetch the File Content**: Your tool should then make a standard HTTP `GET` request to this URL to download the file's content.

## Configuration for Remote MCP Servers

### Local (stdio) MCP Servers
Local MCP servers run on the same machine as the Atlas UI backend and can typically access files using relative URLs or `http://localhost:8000`.

### Remote (HTTP/SSE) MCP Servers
Remote MCP servers run on different machines and need to access the Atlas UI backend over the network. To enable file access for remote servers:

1. **Set `BACKEND_PUBLIC_URL`** environment variable to the publicly accessible URL of your Atlas UI backend:
   ```bash
   # In your .env file or environment
   BACKEND_PUBLIC_URL=https://atlas-ui.example.com
   ```

2. **Ensure network connectivity**: The remote MCP server must be able to make HTTP requests to the backend URL.

3. **Configure MCP server environment**: For stdio servers, you can pass the backend URL via environment variables:
   ```json
   {
     "my-remote-tool": {
       "command": ["python", "mcp/my-remote-tool/main.py"],
       "env": {
         "BACKEND_URL": "${BACKEND_PUBLIC_URL}"
       },
       "groups": ["users"]
     }
   }
   ```

**Note:** If `BACKEND_PUBLIC_URL` is not configured, the backend will generate relative URLs (e.g., `/api/files/download/...`) which only work for local servers.

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
    
    The filename parameter will be rewritten by the Atlas UI backend to a 
    tokenized download URL (either relative for local servers or absolute 
    for remote servers if BACKEND_PUBLIC_URL is configured).
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

## Security Considerations

1. **Token-based access**: Download URLs contain short-lived security tokens (default: 1 hour TTL) that authorize file access without requiring session cookies.
2. **User-scoped access**: Each token is tied to a specific user and file, preventing unauthorized access.
3. **Network security**: For remote MCP servers, ensure proper network security (HTTPS, firewalls) between the server and Atlas UI backend.
4. **Token expiration**: If a file download takes longer than the token TTL, the request will fail. Consider increasing `CAPABILITY_TOKEN_TTL_SECONDS` for large files.

This architecture ensures that your tool does not need to handle any S3 credentials, making the system more secure and easier to develop for.
