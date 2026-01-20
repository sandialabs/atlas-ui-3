# File Access for Remote MCP Servers - Solution Summary

Last updated: 2026-01-19

## Problem

Remote MCP servers (HTTP/SSE) could not access files attached by users in Atlas UI because download URLs were generated as relative paths (e.g., `/api/files/download/key?token=...`) which only work for local/stdio servers running on the same machine as the backend.

## Root Cause

The `create_download_url()` function in `backend/core/capabilities.py` was generating relative URLs by default. Remote MCP servers on different machines had no way to resolve these relative URLs to the actual backend address.

## Solution

### 1. Added BACKEND_PUBLIC_URL Configuration

**New environment variable: `BACKEND_PUBLIC_URL`**
- Purpose: Specifies the publicly accessible URL of the Atlas UI backend
- When set: Generates absolute download URLs for remote MCP server access
- When not set: Generates relative URLs for backward compatibility with local servers

**Example Configuration:**
```bash
# For production with HTTPS
BACKEND_PUBLIC_URL=https://atlas.example.com

# For development
BACKEND_PUBLIC_URL=http://localhost:8000

# With non-standard port
BACKEND_PUBLIC_URL=https://atlas.example.com:8443
```

### 2. Modified URL Generation Logic

Updated `backend/core/capabilities.py` to:
- Check if `BACKEND_PUBLIC_URL` is configured
- Generate absolute URLs when configured (e.g., `https://atlas.example.com/api/files/download/key?token=...`)
- Fall back to relative URLs for backward compatibility
- Handle trailing slashes and various URL formats gracefully

### 3. Enhanced Documentation

**Added comprehensive documentation:**
- `docs/admin/troubleshooting-file-access.md` - Troubleshooting guide with common issues and solutions
- Updated `docs/developer/working-with-files.md` - Configuration instructions for remote MCP servers
- Updated `.env.example` - Detailed configuration comments

**Documentation covers:**
- Configuration for local vs remote MCP servers
- Network connectivity requirements
- Security considerations
- Example configurations for various deployment scenarios
- Troubleshooting common issues

### 4. Comprehensive Testing

**Added 10 test cases in `backend/tests/test_backend_public_url.py`:**
- Relative URL generation without configuration
- Absolute URL generation with configuration
- URL handling with trailing slashes
- Non-standard ports
- Localhost URLs
- Fallback behavior
- Token validation
- Backward compatibility

## How It Works

### For Local (stdio) MCP Servers
1. User attaches file → stored in S3
2. LLM calls tool with `filename` parameter
3. Backend rewrites to relative URL: `/api/files/download/key?token=...`
4. Local MCP server accesses via `http://localhost:8000/api/files/download/...`
5. Works because server is on same machine as backend

### For Remote (HTTP/SSE) MCP Servers
1. User attaches file → stored in S3
2. LLM calls tool with `filename` parameter
3. Backend checks `BACKEND_PUBLIC_URL` configuration
4. Backend rewrites to absolute URL: `https://atlas.example.com/api/files/download/key?token=...`
5. Remote MCP server can access via the public URL
6. Works because server has network path to backend

## Configuration Examples

### Single Server Deployment (Development)
```bash
BACKEND_PUBLIC_URL=http://localhost:8000
DEBUG_MODE=true
```

### Production with Load Balancer
```bash
BACKEND_PUBLIC_URL=https://atlas.company.com
DEBUG_MODE=false
```

### Internal Network Deployment
```bash
BACKEND_PUBLIC_URL=http://internal-atlas.company.local:8000
```

## Security Considerations

1. **Token-based Access**: Download URLs contain short-lived security tokens (default 1-hour TTL)
2. **User-scoped**: Each token is tied to a specific user and file
3. **No credential exposure**: MCP servers don't need S3 credentials
4. **Network Security**: Use HTTPS in production, configure firewalls appropriately

## Backward Compatibility

The solution maintains full backward compatibility:
- Existing deployments without `BACKEND_PUBLIC_URL` continue to work
- Local/stdio servers continue to use relative URLs
- No changes required to existing MCP server code
- All existing tests pass

## Future Enhancements

Added `INCLUDE_FILE_CONTENT_BASE64` configuration option (experimental, disabled by default):
- Would inject base64-encoded file content directly in tool arguments
- Allows MCP servers to access files without network requests
- Disabled by default due to message size concerns
- Requires additional implementation for async context support

## Usage Instructions

### For Administrators

1. **Set `BACKEND_PUBLIC_URL` in your environment:**
   ```bash
   # Add to .env file or set as environment variable
   BACKEND_PUBLIC_URL=https://your-atlas-domain.com
   ```

2. **Restart the Atlas UI backend:**
   ```bash
   # Docker deployment
   docker-compose restart backend
   
   # Local deployment
   # Stop and restart the backend process
   ```

3. **Verify configuration:**
   - Check logs for "Using backend_public_url" messages
   - Test file attachment with remote MCP server tool

### For MCP Server Developers

**No code changes required!** The `filename` parameter in your tools will automatically receive the appropriate URL (relative or absolute) based on the backend configuration.

**Example tool (unchanged):**
```python
@mcp.tool
def process_file(filename: str) -> Dict[str, Any]:
    # filename is automatically a download URL
    response = httpx.get(filename, timeout=30)
    # Process file content...
```

## Troubleshooting

See `docs/admin/troubleshooting-file-access.md` for detailed troubleshooting guide covering:
- Remote server connection issues
- Token expiration errors
- SSL/TLS certificate problems
- Network isolation and firewall blocks
- Configuration verification

## Testing

All tests pass:
- ✅ 10 new tests for BACKEND_PUBLIC_URL configuration
- ✅ 4 existing capability token tests
- ✅ 6 existing file attachment flow tests

## Summary

The issue has been resolved by:
1. Adding `BACKEND_PUBLIC_URL` configuration option
2. Modifying URL generation to use absolute URLs when configured
3. Maintaining backward compatibility with existing deployments
4. Providing comprehensive documentation and troubleshooting guides
5. Adding comprehensive test coverage

Remote MCP servers can now access attached files by configuring the `BACKEND_PUBLIC_URL` environment variable to point to the publicly accessible Atlas UI backend URL.
