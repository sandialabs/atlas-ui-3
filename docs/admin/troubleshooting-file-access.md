# Troubleshooting File Access for MCP Servers

Last updated: 2026-02-02

This guide helps resolve issues with MCP servers accessing attached files in Atlas UI.

## Overview

When users attach files in Atlas UI, those files are stored in S3-compatible storage. MCP servers access these files via tokenized download URLs provided by the backend. The type of URL generated depends on your configuration:

- **Local/stdio servers**: Can use relative URLs (`/api/files/download/...`) or localhost URLs
- **Remote HTTP/SSE servers**: Require absolute URLs with the backend's public address

## Common Issues and Solutions

### Issue 1: Remote MCP Server Cannot Download Files

**Symptoms:**
- MCP server reports HTTP connection errors or timeouts when accessing files
- File download requests return "Connection refused" or "Host not found" errors
- Tools that accept `filename` parameters fail with network errors

**Cause:**
The remote MCP server cannot reach the Atlas UI backend because download URLs are relative (e.g., `/api/files/download/...`) instead of absolute URLs.

**Solution:**
Configure `BACKEND_PUBLIC_URL` in your environment:

1. Add to your `.env` file or set as environment variable:
   ```bash
   BACKEND_PUBLIC_URL=https://atlas-ui.example.com
   ```

2. Restart the Atlas UI backend to apply changes

3. Verify the configuration:
   - Check logs for "Using backend_public_url" messages
   - Test file attachment with a remote MCP server tool

**Note:** The URL should include the protocol (`https://` or `http://`) and any non-standard ports.

### Issue 2: Local MCP Server Works But Remote Server Doesn't

**Symptoms:**
- stdio (local) MCP servers can access files successfully
- HTTP/SSE (remote) MCP servers fail to access the same files
- Different behavior between development and production environments

**Cause:**
Local servers can resolve relative URLs or use `localhost`, but remote servers need the actual backend address.

**Solution:**
1. Set `BACKEND_PUBLIC_URL` for remote servers (see Issue 1)
2. Ensure the backend URL is reachable from the remote server's network
3. Check firewall rules and network connectivity:
   ```bash
   # From the remote MCP server machine:
   curl https://atlas-ui.example.com/api/health
   ```

### Issue 3: Token Expiration Errors

**Symptoms:**
- File download initially works but fails after some time
- Error messages about "expired token" or "invalid token"
- Long-running file processing operations fail

**Cause:**
Download tokens have a default TTL (time-to-live) of 1 hour. Large file downloads or slow processing can exceed this limit.

**Solution:**
Increase the token TTL in your configuration:

1. Add to `.env` file:
   ```bash
   CAPABILITY_TOKEN_TTL_SECONDS=7200  # 2 hours
   ```

2. Restart the backend

3. For very large files or slow operations, consider implementing chunked downloads or streaming in your MCP server

### Issue 4: Files Work in Development But Not Production

**Symptoms:**
- File access works when testing locally
- Production deployment fails to access files
- Different URLs appearing in logs between environments

**Cause:**
`BACKEND_PUBLIC_URL` is not configured in production, or is set to a development URL.

**Solution:**
1. Verify `BACKEND_PUBLIC_URL` in production environment:
   ```bash
   # Should match your production domain
   echo $BACKEND_PUBLIC_URL
   ```

2. Check that the URL matches your actual deployment:
   - ✅ Correct: `https://atlas.mycompany.com`
   - ❌ Wrong: `http://localhost:8000`
   - ❌ Wrong: Missing entirely

3. Ensure load balancer or reverse proxy properly forwards requests to the backend

### Issue 5: SSL/TLS Certificate Errors

**Symptoms:**
- MCP server reports SSL certificate verification errors
- "SSL: CERTIFICATE_VERIFY_FAILED" errors in logs
- File downloads work with HTTP but fail with HTTPS

**Cause:**
The remote MCP server cannot verify the SSL certificate of the Atlas UI backend.

**Solution:**

**For production with valid SSL certificates:**
1. Ensure the SSL certificate is properly installed and valid
2. Verify certificate chain is complete
3. Check that the remote server's CA bundle is up to date

**For development/testing with self-signed certificates:**
1. Update your MCP server to skip SSL verification (development only!):
   ```python
   import httpx
   
   # For development only - never use in production!
   response = httpx.get(url, verify=False)
   ```

2. Or install the self-signed certificate in the MCP server's trust store

### Issue 6: Network Isolation / Firewall Blocks

**Symptoms:**
- Connection timeout errors
- "No route to host" errors
- Works from some machines but not others

**Cause:**
Network firewall or security groups block traffic between the MCP server and Atlas UI backend.

**Solution:**
1. Verify network connectivity:
   ```bash
   # From MCP server machine
   telnet atlas-ui.example.com 443
   # or
   nc -zv atlas-ui.example.com 443
   ```

2. Check and update firewall rules:
   - Allow outbound HTTPS (port 443) from MCP server
   - Allow inbound HTTPS to Atlas UI backend from MCP server IP
   
3. For cloud deployments, check security groups and network ACLs

## Configuration Examples

### Example 1: Single Server Deployment (Development)

```bash
# .env file
BACKEND_PUBLIC_URL=http://localhost:8000
DEBUG_MODE=true
```

MCP servers on the same machine can access files via localhost.

### Example 2: Production with Load Balancer

```bash
# .env file
BACKEND_PUBLIC_URL=https://atlas.company.com
DEBUG_MODE=false
```

All MCP servers (local and remote) access files via the public HTTPS URL.

### Example 3: Internal Network Deployment

```bash
# .env file
BACKEND_PUBLIC_URL=http://internal-atlas.company.local:8000
```

MCP servers on the internal network access files via the internal hostname.

### Example 4: Mixed Environment (Local + Remote)

```bash
# .env file - Backend server
BACKEND_PUBLIC_URL=https://atlas.company.com

# mcp.json - Local stdio server
{
  "local-tool": {
    "command": ["python", "mcp/local-tool/main.py"],
    "transport": "stdio"
  },
  
  # Remote HTTP server
  "remote-tool": {
    "url": "https://remote-mcp.company.com",
    "transport": "http",
    "auth_token": "${MCP_REMOTE_TOKEN}"
  }
}
```

Both local and remote servers work because the backend generates absolute URLs.

### Issue 7: File Upload Fails for Filenames with Spaces

**Symptoms:**
- Uploading a file whose name contains spaces fails silently or produces an error
- S3 tagging headers are malformed

**Cause:**
Prior to PR #284, filenames with whitespace could produce malformed S3 `Tagging` headers because tag values were not URL-encoded.

**Solution:**
As of PR #284, filenames are automatically sanitized: all whitespace characters (spaces, tabs, etc.) are replaced with underscores before storage. This happens in both the frontend (on file select/drop) and the backend (`FileManager.sanitize_filename`). No user action is required -- files like `my report.pdf` will be stored as `my_report.pdf`.

## Debugging Tips

### Enable Detailed Logging

1. Set log level to DEBUG in `.env`:
   ```bash
   LOG_LEVEL=DEBUG
   ```

2. Check backend logs for file URL generation:
   ```bash
   grep "Rewriting filename argument" logs/app.jsonl
   ```

3. Check MCP server logs for HTTP requests

### Test File Access Manually

1. Attach a file in Atlas UI
2. Copy the download URL from the backend logs
3. Test the URL from the MCP server machine:
   ```bash
   curl -v "https://atlas.company.com/api/files/download/key123?token=abc..."
   ```

### Verify Configuration

Check the configuration is loaded correctly:
```python
# In Python
from modules.config import config_manager
settings = config_manager.app_settings
print(f"Backend URL: {settings.backend_public_url}")
```

## Still Having Issues?

If you continue to experience problems:

1. **Check the Atlas UI GitHub Issues**: Search for similar problems
2. **Review your MCP server logs**: Look for specific error messages
3. **Verify network path**: Use `traceroute` or `mtr` to check connectivity
4. **Contact support**: Provide:
   - Backend logs (with sensitive data redacted)
   - MCP server logs
   - Network topology diagram
   - Configuration files (with secrets redacted)
