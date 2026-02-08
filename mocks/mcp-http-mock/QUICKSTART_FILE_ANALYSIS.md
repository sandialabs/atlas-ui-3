# Quick Start Guide - File Analysis Tool

## What This Demonstrates

The `analyze_file` tool in the mcp-http-mock server demonstrates how remote MCP servers can access files attached in Atlas UI using the new `BACKEND_PUBLIC_URL` configuration.

## Setup Steps

### 1. Configure Atlas UI Backend

Edit your `.env` file:
```bash
# Set the public URL where your Atlas UI backend is accessible
BACKEND_PUBLIC_URL=https://atlas.example.com

# Or for local testing
BACKEND_PUBLIC_URL=http://localhost:8000
```

### 2. Start the MCP Mock Server

```bash
cd mocks/mcp-http-mock
python main.py
```

This starts the HTTP server on `http://localhost:8005/mcp`

### 3. Configure in Atlas UI

Add to your `config/mcp.json`:
```json
{
  "mcp-http-mock": {
    "enabled": true,
    "description": "HTTP MCP Mock Server with file analysis",
    "groups": ["users"],
    "url": "http://localhost:8005/mcp",
    "transport": "http",
    "auth_token": "test-api-key-123"
  }
}
```

### 4. Restart Atlas UI Backend

```bash
# Restart to pick up the new configuration
docker-compose restart backend
# Or if running locally, restart the backend process
```

## Testing the File Analysis Tool

### Test 1: Text File

1. In Atlas UI chat, click the attachment icon
2. Attach a text file (e.g., `notes.txt`)
3. Ask the LLM: "Can you analyze the attached file?"

Expected result:
```json
{
  "results": {
    "success": true,
    "filename": "notes.txt",
    "file_size_bytes": 1234,
    "file_size_kb": 1.21,
    "content_type": "text/plain",
    "is_text_file": true,
    "text_preview": "First 500 characters of the file...",
    "line_count": 42,
    "access_method": "URL download (remote MCP server compatible)",
    "note": "File was successfully downloaded from backend using tokenized URL"
  }
}
```

### Test 2: Binary File

1. Attach an image or PDF
2. Ask: "What can you tell me about this file?"

Expected result:
```json
{
  "results": {
    "success": true,
    "filename": "image.png",
    "file_size_bytes": 52341,
    "file_size_kb": 51.11,
    "content_type": "image/png",
    "is_text_file": false,
    "text_preview": "<Binary content, cannot display as text>",
    "access_method": "URL download (remote MCP server compatible)"
  }
}
```

## How It Works

### Without BACKEND_PUBLIC_URL (Old Behavior)
```
User attaches file → Backend stores in S3
LLM calls tool(filename="file.txt")
Backend rewrites: /api/files/download/key?token=...
Remote server tries to access: /api/files/download/...
❌ FAILS - Cannot resolve relative URL
```

### With BACKEND_PUBLIC_URL (New Behavior)
```
User attaches file → Backend stores in S3
LLM calls tool(filename="file.txt")
Backend rewrites: https://atlas.example.com/api/files/download/key?token=...
Remote server accesses: https://atlas.example.com/api/files/download/...
✅ SUCCESS - Downloads and processes file
```

## Demo Script

Run the included demo script to see examples:
```bash
python test_file_analysis.py
```

This shows:
- How URL rewriting works
- Example outputs for different file types
- Configuration requirements
- Network connectivity needs

## Troubleshooting

### Error: "HTTP error downloading file: 404"
**Cause:** File not found or token expired
**Solution:** 
- Check that BACKEND_PUBLIC_URL matches your actual backend URL
- Verify file was successfully uploaded
- Check token hasn't expired (default 1 hour)

### Error: "Network error accessing file"
**Cause:** Cannot reach backend URL
**Solution:**
- Verify BACKEND_PUBLIC_URL is correct and accessible
- Check firewall rules allow traffic
- Test with: `curl https://atlas.example.com/api/health`

### Error: "Connection refused"
**Cause:** Backend not accessible from MCP server
**Solution:**
- If testing locally, ensure backend is running
- If remote deployment, check network connectivity
- Verify DNS resolution of the backend URL

## Code Reference

The analyze_file tool implementation in `main.py`:
```python
@mcp.tool
def analyze_file(
    filename: Annotated[str, "File URL or path..."],
    username: Annotated[str, "User identity..."] = "",
) -> str:
    # Download file from URL
    response = requests.get(filename, timeout=30)
    response.raise_for_status()
    content = response.content
    
    # Analyze file
    file_size = len(content)
    # ... more analysis
    
    return json.dumps({"results": analysis})
```

## Next Steps

1. **Production Deployment:**
   - Set BACKEND_PUBLIC_URL to your production domain
   - Use HTTPS for security
   - Configure proper firewall rules

2. **Custom MCP Servers:**
   - Use this tool as a template
   - Implement your own file processing logic
   - Follow the same URL handling pattern

3. **Advanced Use Cases:**
   - PDF text extraction (see atlas/mcp/pdfbasic)
   - CSV data analysis (see atlas/mcp/csv_reporter)
   - Image processing
   - Document conversion
