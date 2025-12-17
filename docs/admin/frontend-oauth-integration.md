# OAuth 2.1 Frontend Integration

## Current Status

OAuth 2.1 authentication is currently implemented at the **backend level only**. The OAuth flow is triggered automatically when the MCP client connects to an OAuth-protected server.

## How It Works Now

### Backend OAuth Flow

When a user tries to use an MCP server configured with OAuth:

1. **Backend detects OAuth config** in `mcp.json`:
```json
{
  "oauth-server": {
    "url": "https://fastmcp.cloud/mcp",
    "transport": "http",
    "oauth_config": {
      "enabled": true,
      "scopes": "read write"
    }
  }
}
```

2. **FastMCP's OAuth helper** automatically:
   - Opens user's default browser
   - Starts local callback server
   - Directs user to OAuth authorization page
   - Captures authorization code
   - Exchanges for access token
   - Stores encrypted token

3. **MCP client uses token** for all subsequent requests

### Limitations

- OAuth flow happens in **backend process context**
- Browser opens on the **server machine**, not user's browser
- Not suitable for web-based deployments
- Works only for local/desktop deployments

## Future: Frontend OAuth Integration

For true web-based OAuth, the flow needs to be browser-initiated.

### Proposed Architecture

#### 1. OAuth Trigger from Frontend

Instead of backend opening a browser, the frontend initiates OAuth:

```javascript
// When user clicks "Connect" on OAuth server
const initiateOAuth = async (serverName) => {
  // Request OAuth authorization URL from backend
  const response = await fetch(`/api/mcp/oauth/authorize/${serverName}`)
  const { authorization_url, state } = await response.json()
  
  // Open OAuth page in new window
  const authWindow = window.open(authorization_url, 'oauth', 'width=600,height=800')
  
  // Listen for callback
  window.addEventListener('message', handleOAuthCallback)
}
```

#### 2. Backend OAuth Endpoints

New endpoints needed:

**GET `/api/mcp/oauth/authorize/{server_name}`**
- Generates OAuth authorization URL
- Creates PKCE code verifier/challenge
- Stores state in session
- Returns URL for frontend to open

**POST `/api/mcp/oauth/callback`**
- Receives authorization code from frontend
- Exchanges code for access token
- Stores encrypted token
- Returns success status

**GET `/api/mcp/oauth/status/{server_name}`**
- Checks if OAuth token exists
- Returns token validity status
- Used by frontend to show "Connected" status

#### 3. OAuth Callback Page

New frontend route `/oauth/callback` to handle OAuth redirects:

```javascript
// /oauth/callback page
useEffect(() => {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('code')
  const state = params.get('state')
  
  // Send code to backend
  fetch('/api/mcp/oauth/callback', {
    method: 'POST',
    body: JSON.stringify({ code, state })
  }).then(() => {
    // Notify parent window
    window.opener.postMessage({ type: 'oauth_success' }, '*')
    window.close()
  })
}, [])
```

#### 4. UI Components

**OAuth Status Indicator**
```jsx
const OAuthStatusBadge = ({ serverName }) => {
  const [status, setStatus] = useState('disconnected')
  
  useEffect(() => {
    fetch(`/api/mcp/oauth/status/${serverName}`)
      .then(r => r.json())
      .then(data => setStatus(data.connected ? 'connected' : 'disconnected'))
  }, [serverName])
  
  return (
    <span className={status === 'connected' ? 'text-green-500' : 'text-gray-500'}>
      {status === 'connected' ? 'OAuth Connected' : 'Not Connected'}
    </span>
  )
}
```

**OAuth Connect Button**
```jsx
const OAuthConnectButton = ({ serverName }) => {
  const handleConnect = async () => {
    const response = await fetch(`/api/mcp/oauth/authorize/${serverName}`)
    const { authorization_url } = await response.json()
    
    const width = 600
    const height = 800
    const left = window.screen.width / 2 - width / 2
    const top = window.screen.height / 2 - height / 2
    
    window.open(
      authorization_url,
      'oauth',
      `width=${width},height=${height},left=${left},top=${top}`
    )
  }
  
  return (
    <button onClick={handleConnect} className="btn-oauth">
      Connect with OAuth
    </button>
  )
}
```

### Implementation Steps

1. **Phase 1: Backend API**
   - Add `/api/mcp/oauth/authorize/{server}` endpoint
   - Add `/api/mcp/oauth/callback` endpoint
   - Add `/api/mcp/oauth/status/{server}` endpoint
   - Add session management for OAuth state

2. **Phase 2: Frontend Routes**
   - Create `/oauth/callback` page
   - Add OAuth window management
   - Add message passing for callback

3. **Phase 3: UI Components**
   - Add OAuth status indicators to ToolsPanel
   - Add OAuth connect buttons
   - Add visual feedback for OAuth flow

4. **Phase 4: Integration**
   - Update MCP client to check OAuth status
   - Handle token refresh in background
   - Add error handling and retry logic

### Security Considerations

1. **PKCE Required**: Always use PKCE for public clients
2. **State Validation**: Verify OAuth state to prevent CSRF
3. **Popup Blockers**: Handle popup blocking gracefully
4. **Token Storage**: Keep tokens in backend, never expose to frontend
5. **HTTPS Required**: OAuth must use HTTPS in production

### Configuration Example

```json
{
  "oauth-server": {
    "url": "https://api.example.com/mcp",
    "transport": "http",
    "oauth_config": {
      "enabled": true,
      "scopes": "mcp:read mcp:write",
      "client_name": "Atlas UI",
      "frontend_callback": true,  // New: Use frontend-initiated flow
      "callback_url": "https://atlas.example.com/oauth/callback"
    }
  }
}
```

### Testing

1. **Local Development**:
```env
# Use localhost for callback
OAUTH_CALLBACK_URL=http://localhost:3000/oauth/callback
```

2. **Production**:
```env
# Use production domain
OAUTH_CALLBACK_URL=https://atlas.example.com/oauth/callback
```

## Current Workaround

For now, to use OAuth with Atlas UI:

1. **Local/Desktop Deployment**: Works as-is
2. **Server Deployment**: 
   - Use JWT upload instead of OAuth
   - Get JWT from OAuth server separately
   - Upload via UI or API

## Related Documentation

- Backend OAuth: `docs/admin/mcp-oauth.md`
- JWT Upload: `docs/admin/OAuth-JWT-README.md`
- Storage Backends: `docs/admin/future-storage-backends.md`
