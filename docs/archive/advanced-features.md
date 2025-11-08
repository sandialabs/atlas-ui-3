# Advanced Features

This document covers advanced features and capabilities of the Chat UI application.

## Custom HTML/UI Modification by MCP Servers

### Overview
MCP servers can dynamically modify the UI by returning custom HTML content that gets rendered in the Canvas panel.

### How It Works

1. **MCP Server Returns Custom HTML**: 
   ```python
   @mcp.tool
   def create_custom_ui() -> dict:
       """Create custom UI content."""
       custom_html = """
       <div style="background: #2d3748; padding: 20px; border-radius: 10px;">
           <h3>Custom UI from MCP Server</h3>
           <button onclick="alert('Hello from MCP!')">Click Me!</button>
       </div>
       """
       
       return {
           "content": "Custom UI created successfully!",
           "custom_html": custom_html,
           "success": True
       }
   ```

2. **Backend Processes Response**:
   - Automatically detects `custom_html` fields in MCP tool responses
   - Sends `custom_ui` update to frontend via WebSocket
   - Logs UI modification activity

3. **Frontend Renders Content**:
   - Sanitizes HTML using DOMPurify for security
   - Renders in Canvas panel
   - Auto-opens Canvas when custom content received

### Security Features
- **HTML Sanitization**: All custom HTML processed through DOMPurify
- **Safe Rendering**: Only safe HTML elements and attributes allowed
- **Isolated Execution**: JavaScript runs in browser context but cannot access sensitive APIs

### Use Cases

**Data Visualizations**:
```python
@mcp.tool
def create_chart(data: list) -> dict:
    """Create a bar chart visualization."""
    bars = ""
    for item in data:
        height = item['value'] * 3  # Scale for display
        bars += f'<div style="height: {height}px; background: #4299e1; margin: 5px; display: inline-block; width: 30px;"></div>'
    
    custom_html = f"""
    <div style="padding: 20px;">
        <h3>Data Visualization</h3>
        <div style="display: flex; align-items: end; height: 200px;">
            {bars}
        </div>
    </div>
    """
    
    return {
        "content": "Chart created successfully",
        "custom_html": custom_html
    }
```

**Interactive Forms**:
```python
@mcp.tool
def create_form() -> dict:
    """Create an interactive form."""
    custom_html = """
    <div style="padding: 20px; background: #f7fafc; border-radius: 8px;">
        <h3>User Input Form</h3>
        <form onsubmit="alert('Form submitted!'); return false;">
            <div style="margin: 10px 0;">
                <label>Name:</label>
                <input type="text" style="margin-left: 10px; padding: 5px;">
            </div>
            <div style="margin: 10px 0;">
                <label>Email:</label>
                <input type="email" style="margin-left: 10px; padding: 5px;">
            </div>
            <button type="submit" style="background: #4299e1; color: white; padding: 8px 16px; border: none; border-radius: 4px;">Submit</button>
        </form>
    </div>
    """
    
    return {
        "content": "Interactive form created",
        "custom_html": custom_html
    }
```

**Rich Content Display**:
```python
@mcp.tool
def display_file_content(filepath: str) -> dict:
    """Display file with custom formatting."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    custom_html = f"""
    <div style="max-height: 400px; overflow-y: auto;">
        <h3>File: {filepath}</h3>
        <pre style="background: #2d3748; color: #e2e8f0; padding: 15px; border-radius: 5px; font-family: 'Courier New', monospace;">
{content}
        </pre>
        <button onclick="navigator.clipboard.writeText(`{content}`)" 
                style="margin-top: 10px; background: #48bb78; color: white; padding: 5px 10px; border: none; border-radius: 3px;">
            Copy to Clipboard
        </button>
    </div>
    """
    
    return {
        "content": f"Displaying {filepath}",
        "custom_html": custom_html,
        "file_path": filepath
    }
```

## Agent Mode

### Overview
Agent mode enables multi-step reasoning where the AI can break down complex tasks into steps and execute them systematically.

### Configuration
Enable in `.env`:
```bash
AGENT_MODE_AVAILABLE=true
AGENT_MAX_STEPS=10
```

### Features
- **Step-by-step reasoning**: AI breaks down complex tasks
- **Progress tracking**: Visual indicators of reasoning progress
- **Tool integration**: Can use MCP tools during reasoning
- **Interruption capability**: Users can stop reasoning process

### Usage
1. Enable agent mode in the UI
2. Provide a complex task or question
3. AI will break it down into steps and execute them
4. View progress and intermediate results

## RAG (Retrieval-Augmented Generation)

### Overview
RAG integration allows the AI to access and query external document sources for enhanced responses.

### Configuration

**Mock RAG Service** (development):
```bash
MOCK_RAG=true
RAG_MOCK_URL=http://localhost:8001
```

**Production RAG Service**:
```bash
MOCK_RAG=false
RAG_MOCK_URL=https://your-rag-service.com/api
```

### Features
- **Document Selection**: Choose from available data sources
- **RAG-only Mode**: Query only documents, bypass LLM
- **Integrated Mode**: Combine document retrieval with LLM reasoning
- **Source Attribution**: Track which documents informed responses

### Mock RAG Service
A mock service is included for testing:
```bash
cd mocks/rag-mock
python mock_rag_server.py
```

## System Banners

### Overview
System administrators can display informational banners to users for announcements, maintenance notices, etc.

### Configuration
```bash
BANNER_ENABLED=true
```

### Banner Service API
The banner service should return JSON:
```json
{
  "messages": [
    "System maintenance scheduled for tonight 11 PM - 1 AM PST",
    "New MCP servers available in the marketplace",
    "Known issue with RAG service - ETA for fix: 2 hours"
  ]
}
```

### Mock Banner Service
A mock service is included:
```bash
cd mocks/sys-admin-mock
python banner_server.py
```

Messages are read from `messages.txt`, one per line.

## Canvas Panel

### Overview
The Canvas panel provides a flexible area for displaying custom content, visualizations, and interactive elements.

### Features
- **Resizable**: Adjust width to balance with chat interface
- **Auto-opening**: Automatically opens when custom content received
- **Security**: All content sanitized before rendering
- **Persistence**: Retains content across sessions

### Usage
- Custom HTML from MCP servers renders here
- Can display images, charts, forms, and interactive content
- Supports responsive design for different screen sizes

## Advanced Authentication

### JWT Authentication (Planned)
Currently, WebSocket authentication relies on the `x-email-header`, which could be spoofed. Future enhancement will implement JWT tokens.

**Planned Implementation**:
```python
from fastapi import Depends
from jose import JWTError, jwt

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        return email
    except JWTError:
        raise credentials_exception
```

### Authorization Groups
Fine-grained access control through user groups:

- **admin**: Full system access
- **users**: Standard user access
- **mcp_basic**: Basic MCP server access
- **mcp_advanced**: Advanced MCP server access

## Marketplace

### Overview
The marketplace allows users to select which MCP servers they want to use, providing a personalized toolset.

### Features
- **Server Selection**: Choose from authorized MCP servers
- **Persistent Selection**: Choices saved in browser localStorage
- **Authorization Aware**: Only shows servers user has access to
- **Description Display**: Each server shows its capabilities

### Usage
1. Navigate to the marketplace (separate route)
2. Select desired MCP servers with checkboxes
3. Return to main interface
4. Only selected servers appear in Tools panel

## Performance Features

### Unified HTTP Client
- **Consistent Error Handling**: Standardized HTTP responses
- **Comprehensive Logging**: Full tracebacks for debugging
- **Retry Logic**: Automatic retry for transient failures
- **Timeout Management**: Configurable request timeouts

### Configuration System v2.0
- **Type Safety**: Pydantic models with validation
- **Centralized Management**: Single source of truth
- **Environment Integration**: Seamless .env loading
- **Hot Reload**: Configuration changes without restart (planned)

## Offline Deployment

### Overview
The application can be configured for deployment in environments without internet access.

### Setup Process
1. **Download Dependencies**:
   ```bash
   python scripts/download-deps.py
   ```

2. **Manual Configuration**:
   - Remove Google Fonts CDN links from `index.html`
   - Remove source map references from `purify.min.js`
   - Configure vendor directory mounts in `main.py`

### Features
- **Self-contained**: All dependencies included locally
- **No External Calls**: Eliminates all CDN dependencies
- **Vendor Management**: Local hosting of JavaScript libraries

## Development Tools

### Enhanced Logging
- **Full Tracebacks**: All errors include complete stack traces
- **Security Auditing**: Special logging for authorization failures
- **Consistent Format**: Standardized logging across modules
- **File Rotation**: Automatic log file management

### Code Quality
- **400-line Limit**: Maximum file size for maintainability
- **Modular Design**: Highly modular architecture
- **Type Safety**: Pydantic models throughout
- **Linting**: Ruff for Python, ESLint for JavaScript

### Testing Infrastructure
- **Unit Tests**: Focused testing for individual components
- **Mock Services**: RAG and banner mock services for testing
- **Configuration Testing**: Validation of configuration loading
- **Integration Points**: Well-defined interfaces for testing

## Future Enhancements

### Multi-modal Support
- **Image Processing**: AI analysis of uploaded images
- **Audio Support**: Voice input and synthesis
- **Document Processing**: PDF, Word, and other formats

### Advanced Integrations
- **GitHub Integration**: Repository analysis and code assistance
- **Slack/Teams**: Direct integration with communication platforms
- **API Webhooks**: External system notifications

### Performance Improvements
- **Caching**: Response and computation caching
- **Load Balancing**: Multiple backend instance support
- **Database Integration**: Persistent storage for configurations