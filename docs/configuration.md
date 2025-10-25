# Configuration Guide

Comprehensive guide to configuring the Chat UI application.

## Configuration Architecture

The project uses a **modern Pydantic-based configuration system** that provides:
- **Type-safe** configuration with automatic validation
- **Centralized** management of all settings  
- **Environment integration** with .env file loading
- **Single source of truth** for all configuration

## Configuration Files

### 1. Environment Variables (.env)

Create from template:
```bash
cp .env.example .env
```

**Application Settings**:
```bash
# Application Settings
DEBUG_MODE=true              # Skip authentication in development  
PORT=8000                   # Server port
APP_NAME="Chat UI"          # Application name

# RAG Settings
MOCK_RAG=true               # Use mock RAG service for testing
RAG_MOCK_URL=http://localhost:8001  # RAG service URL

# Agent Settings
AGENT_MODE_AVAILABLE=false  # Enable agent mode UI
AGENT_MAX_STEPS=10          # Maximum agent reasoning steps

# LLM Health Check Settings
LLM_HEALTH_CHECK_INTERVAL=5  # Health check interval in minutes (0 = disabled)

# API Keys (used by LLM config)
OPENAI_API_KEY=your_key     # OpenAI API key
ANTHROPIC_API_KEY=your_key  # Anthropic API key
GOOGLE_API_KEY=your_key     # Google API key

# Banner Settings (optional)
BANNER_ENABLED=false        # Enable system banners
```

### 2. LLM Configuration (llmconfig.yml)

Configure available language models:

```yaml
models:
  gpt-4:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-4"
    api_key: "${OPENAI_API_KEY}"
    description: "GPT-4 by OpenAI"
    max_tokens: 2000
    temperature: 0.7

  gpt-3.5-turbo:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-3.5-turbo"
    api_key: "${OPENAI_API_KEY}"
    description: "GPT-3.5 Turbo"
    max_tokens: 1000
    temperature: 0.7

  claude-3-sonnet:
    model_url: "https://api.anthropic.com/v1/messages"
    model_name: "claude-3-sonnet-20240229"
    api_key: "${ANTHROPIC_API_KEY}"
    description: "Claude 3 Sonnet"
    max_tokens: 1500
    temperature: 0.8

  gemini-pro:
    model_url: "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"
    model_name: "gemini-pro"
    api_key: "${GOOGLE_API_KEY}"
    description: "Google Gemini Pro"
    max_tokens: 1000
    temperature: 0.9
```

**Model Configuration Options**:
- `model_url`: API endpoint for the model
- `model_name`: Model identifier for API calls
- `api_key`: API key (can use environment variable substitution)
- `description`: Human-readable description
- `max_tokens`: Maximum tokens per response
- `temperature`: Response creativity (0.0-1.0)

### 3. MCP Configuration (mcp.json)

Configure MCP (Model Context Protocol) servers:

```json
{
  "filesystem": {
    "groups": ["users", "mcp_basic"],
    "is_exclusive": false,
    "description": "File system read/write operations",
    "enabled": true
  },
  "calculator": {
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Mathematical calculations",
    "enabled": true
  },
  "secure": {
    "groups": ["admin"],
    "is_exclusive": true,
    "description": "Secure system operations",
    "enabled": true
  },
  "thinking": {
    "groups": ["users", "mcp_basic"],
    "is_exclusive": false,
    "description": "Structured thinking and reasoning",
    "enabled": true
  },
  "duckduckgo": {
    "groups": ["users", "mcp_basic"],
    "is_exclusive": false,
    "description": "Web search via DuckDuckGo",
    "enabled": true
  }
}
```

**MCP Server Options**:
- `groups`: User groups that can access this server
- `is_exclusive`: If true, prevents other servers from running simultaneously
- `description`: Description shown in the UI
- `enabled`: Whether the server is available

**Available User Groups**:
- `admin`: Administrative users
- `users`: Regular users
- `mcp_basic`: Basic MCP access
- `mcp_advanced`: Advanced MCP access

## Accessing Configuration in Code

### Backend Configuration
```python
from config import config_manager

# Get application settings
app_settings = config_manager.app_settings
print(f"Running {app_settings.app_name} on port {app_settings.port}")

# Get LLM configuration
llm_config = config_manager.llm_config
models = list(llm_config.models.keys())

# Get MCP configuration
mcp_config = config_manager.mcp_config
servers = list(mcp_config.servers.keys())

# Check if debug mode is enabled
if app_settings.debug_mode:
    print("Running in debug mode")
```

### Configuration Validation
The system automatically validates configuration:
```python
# Test configuration loading
python -c "from config import config_manager; print('✅ Config OK')"
```

## Authentication Configuration

### Development Mode
For development, enable debug mode to skip authentication:
```bash
DEBUG_MODE=true
```

### Production Mode
In production, set up reverse proxy authentication:
```bash
DEBUG_MODE=false
```

The application expects the `x-email-header` from your reverse proxy.

### User Group Configuration
Edit `backend/auth.py` to configure user group assignments:

```python
def get_user_groups(user_email: str) -> List[str]:
    """Get groups for a user based on email."""
    
    # Example group assignments
    if user_email.endswith("@admin.com"):
        return ["admin", "users", "mcp_basic", "mcp_advanced"]
    elif user_email.endswith("@company.com"):
        return ["users", "mcp_basic"]
    else:
        return ["users"]
```

## System Prompt Configuration

Customize AI assistant behavior by editing `prompts/system_prompt.md` (now stored at repo root):

```markdown
You are a helpful AI assistant integrated with the Chat UI system.

User: {user_email}

You have access to various tools and can:
- Read and write files
- Perform calculations
- Search the web
- Create custom UI elements

Always be helpful, accurate, and considerate of the user's context.
```

**Features**:
- Supports `{user_email}` placeholder for personalization
- Changes take effect immediately for new conversations
- No server restart required

## RAG Configuration

### Mock RAG Service
For development and testing:
```bash
MOCK_RAG=true
RAG_MOCK_URL=http://localhost:8001
```

### Production RAG Service
For production deployment:
```bash
MOCK_RAG=false
RAG_MOCK_URL=https://your-rag-service.com/api
```

The RAG service should implement the expected API endpoints for document retrieval and processing.

### RAG over MCP (Discovery Phase)

Enable the MCP-backed RAG discovery aggregator to source data sources from MCP servers implementing `rag_discover_resources`:

```bash
FEATURE_RAG_MCP_ENABLED=true
```

When enabled, `/api/config` will return `data_sources` as server-qualified IDs like `serverName:resourceId`. The legacy mock RAG client is used when this flag is off.

## Banner Configuration

System administrators can display banners to users:

```bash
BANNER_ENABLED=true
```

The banner service should return JSON with banner messages:
```json
{
  "messages": [
    "System maintenance scheduled for tonight 11 PM - 1 AM PST",
    "New features available in the Tools panel"
  ]
}
```

## Offline Deployment

For deployments without internet access:

1. **Download dependencies**:
   ```bash
   python scripts/download-deps.py
   ```

2. **Manual edits required**:
   - Remove Google Fonts CDN links from `index.html`
   - Remove source map references to prevent CDN lookups
   - Configure vendor directory mounts in `main.py`

## Configuration Troubleshooting

### Common Issues

1. **Configuration validation errors**:
   ```bash
   # Test configuration
   python -c "from config import config_manager; print(config_manager.app_settings)"
   ```

2. **Environment variable not loading**:
   - Check `.env` file exists and is in the correct location
   - Verify variable names match exactly
   - Ensure no extra spaces around `=`

3. **LLM model not available**:
   - Verify API key is set correctly
   - Check model URL and name
   - Test API key independently

4. **MCP server not appearing**:
   - Check `mcp.json` syntax is valid JSON
   - Verify user groups match your assignments
   - Ensure server `enabled: true`

### Debug Commands

```bash
# Check environment variables
env | grep -E "(OPENAI|ANTHROPIC|GOOGLE|DEBUG)"

# Test configuration loading
cd backend
python -c "from config import config_manager; print('✅ Config loaded successfully')"

# Validate specific configuration sections
python -c "from config import config_manager; print(f'Models: {list(config_manager.llm_config.models.keys())}')"

python -c "from config import config_manager; print(f'MCP Servers: {list(config_manager.mcp_config.servers.keys())}')"
```

## Advanced Configuration

### Custom Model Providers
Add support for new LLM providers by extending the configuration:

```yaml
models:
  custom-model:
    model_url: "https://api.customprovider.com/v1/chat"
    model_name: "custom-model-name"
    api_key: "${CUSTOM_API_KEY}"
    description: "Custom model provider"
    max_tokens: 2000
    temperature: 0.7
    # Custom headers if needed
    headers:
      "Custom-Header": "value"
```

### Environment-Specific Configuration
Use different configurations for different environments:

```bash
# .env.development
DEBUG_MODE=true
MOCK_RAG=true
LOG_LEVEL=DEBUG

# .env.production  
DEBUG_MODE=false
MOCK_RAG=false
LOG_LEVEL=INFO
```

Load with:
```bash
# Development
cp .env.development .env

# Production
cp .env.production .env
```

## LLM Health Check

The system includes automatic health monitoring for all configured LLM models.

### Configuration

Configure health check behavior in your `.env` file:

```bash
# LLM Health Check Settings
LLM_HEALTH_CHECK_INTERVAL=5  # Check interval in minutes (0 = disabled)
```

### How It Works

1. **Startup Check**: Health checks run automatically when the server starts
2. **Periodic Checks**: Continue running every N minutes (configurable)
3. **Simple Test**: Sends a minimal "Hi" prompt to each model
4. **Response Validation**: Expects a non-empty response within reasonable time
5. **Concurrent Execution**: All models checked simultaneously for efficiency

### Monitoring Health Status

**API Endpoint**: Access real-time health status via the API:

```bash
curl -H "X-User-Email: user@example.com" \
     http://localhost:8000/api/llm-health
```

**Response Format**:
```json
{
  "status": "healthy",
  "overall_healthy": true,
  "healthy_count": 2,
  "total_count": 2,
  "last_check": "2024-01-15T10:30:45.123456",
  "models": {
    "gpt-4": {
      "healthy": true,
      "response_time_ms": 245.7,
      "last_check": "2024-01-15T10:30:45.123456",
      "error": null
    },
    "claude-3": {
      "healthy": false,
      "response_time_ms": 5000.0,
      "last_check": "2024-01-15T10:30:44.987654", 
      "error": "Connection timeout"
    }
  }
}
```

### Health Check Logs

Monitor health status in the application logs:

```
2024-01-15 10:30:45 - llm_health_check - INFO - Running health checks for 2 models: ['gpt-4', 'claude-3']
2024-01-15 10:30:45 - llm_health_check - INFO - Health check for model 'gpt-4': ✓ HEALTHY (245.7ms)
2024-01-15 10:30:45 - llm_health_check - ERROR - Health check for model 'claude-3': ✗ FAILED (5000.0ms) - Connection timeout
2024-01-15 10:30:45 - llm_health_check - INFO - Health check completed: 1/2 models healthy
```

### Troubleshooting

**Common Issues**:

1. **All models unhealthy**: Check API keys and network connectivity
2. **Specific model failing**: Verify model configuration in `llmconfig.yml`
3. **Health checks disabled**: Ensure `LLM_HEALTH_CHECK_INTERVAL > 0`
4. **High response times**: Consider network latency or model load

**Disable Health Checks**:
```bash
# Disable by setting interval to 0
LLM_HEALTH_CHECK_INTERVAL=0
```
```