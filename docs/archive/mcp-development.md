# MCP Development Guide

Guide for developing MCP (Model Context Protocol) servers for the Chat UI application.

## What is MCP?

MCP (Model Context Protocol) enables LLMs to connect to external data sources and tools. MCP servers provide specific functionality that can be called during chat conversations.

## Built-in MCP Servers

### Filesystem Server
**Location**: `backend/mcp/filesystem/`

**Tools**:
- `read_file`: Read file contents
- `write_file`: Write content to file
- `list_directory`: List directory contents
- `create_directory`: Create new directory
- `delete_file`: Delete a file
- `file_exists`: Check if file exists

**Security**: Path validation prevents directory traversal

### Calculator Server
**Location**: `backend/mcp/calculator/`

**Tools**:
- `add`, `subtract`, `multiply`, `divide`: Basic arithmetic
- `power`: Exponentiation
- `sqrt`: Square root
- `factorial`: Factorial calculation
- `evaluate`: Safe expression evaluation

**Security**: Restricted builtins for safe evaluation

### UI Demo Server
**Location**: `backend/mcp/ui_demo/`

**Tools**:
- `create_button_demo`: Interactive buttons with JavaScript
- `create_data_visualization`: CSS-based bar charts
- `create_form_demo`: Interactive forms with validation

**Purpose**: Demonstrates custom HTML/UI modification capabilities

## Creating New MCP Servers

### 1. Basic Server Structure

Create a new directory in `backend/mcp/`:
```bash
mkdir backend/mcp/myserver
cd backend/mcp/myserver
```

Create `main.py`:
```python
from fastmcp import FastMCP

mcp = FastMCP(name="MyServer")

@mcp.tool
def my_tool(param: str) -> str:
    """Tool description that appears in the UI."""
    return f"Result: {param}"

@mcp.tool
def another_tool(number: int, text: str = "default") -> dict:
    """Another tool with multiple parameters."""
    return {
        "number": number,
        "text": text,
        "result": number * len(text)
    }

if __name__ == "__main__":
    mcp.run()
```

### 2. Server Configuration

Add your server to `mcp.json`:
```json
{
  "myserver": {
    "groups": ["users", "mcp_basic"],
    "is_exclusive": false,
    "description": "My custom MCP server",
    "enabled": true
  }
}
```

**Configuration options**:
- `groups`: User groups that can access this server
- `is_exclusive`: If true, prevents other servers from running simultaneously
- `description`: Description shown in the UI
- `enabled`: Whether the server is available

### 3. Advanced Features

#### Custom HTML/UI Modification
Return custom HTML that gets rendered in the Canvas panel:

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

#### File Operations with Custom HTML
Combine file operations with custom UI:

```python
@mcp.tool
def read_and_display_file(filepath: str) -> dict:
    """Read a file and display it with custom formatting."""
    try:
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Create custom HTML display
        custom_html = f"""
        <div class="file-display">
            <h3>File: {filepath}</h3>
            <pre style="background: #f4f4f4; padding: 10px; border-radius: 5px;">
{content}
            </pre>
        </div>
        """
        
        return {
            "content": f"Successfully read {filepath}",
            "custom_html": custom_html,
            "file_path": filepath,
            "size": len(content)
        }
    except Exception as e:
        return {"error": str(e)}
```

#### Type Validation
Use Pydantic models for complex parameters:

```python
from pydantic import BaseModel
from typing import Optional, List

class SearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 10
    categories: List[str] = []

@mcp.tool
def search_data(request: SearchRequest) -> dict:
    """Search with complex parameters."""
    return {
        "query": request.query,
        "limit": request.limit,
        "categories": request.categories,
        "results": ["result1", "result2"]
    }
```

## Authorization and Security

### User Groups
Configure which user groups can access your server:

```json
{
  "myserver": {
    "groups": ["users", "admin"],  // Only users and admin groups
    "is_exclusive": false,
    "description": "Admin-only server",
    "enabled": true
  }
}
```

Available groups:
- `admin`: Administrative users
- `users`: Regular users  
- `mcp_basic`: Basic MCP access
- `mcp_advanced`: Advanced MCP access

### Exclusive Servers
Some servers should not run simultaneously (e.g., for security):

```json
{
  "sensitive_server": {
    "groups": ["admin"],
    "is_exclusive": true,  // Prevents other servers from running
    "description": "Sensitive operations",
    "enabled": true
  }
}
```

### Input Validation
Always validate inputs in your tools:

```python
import os
from pathlib import Path

@mcp.tool
def safe_read_file(filepath: str) -> dict:
    """Safely read a file with path validation."""
    # Validate path
    try:
        path = Path(filepath).resolve()
        # Ensure path is within allowed directory
        allowed_dir = Path("/allowed/directory").resolve()
        if not str(path).startswith(str(allowed_dir)):
            return {"error": "Access denied: path outside allowed directory"}
        
        if not path.exists():
            return {"error": "File not found"}
        
        with open(path, 'r') as f:
            content = f.read()
        
        return {"content": content, "path": str(path)}
    except Exception as e:
        return {"error": f"Failed to read file: {str(e)}"}
```

## Testing MCP Servers

### 1. Direct Testing
Test your server directly:

```bash
cd backend/mcp/myserver
python main.py
```

### 2. Integration Testing
Test through the Chat UI:
1. Start the backend
2. Open the Tools panel
3. Select your server
4. Test tool execution

### 3. Command Line Testing
Test tools via command line:

```python
# test_myserver.py
from myserver.main import mcp

# Test tool directly
result = mcp.call_tool("my_tool", {"param": "test"})
print(result)
```

## Common Patterns

### 1. Data Processing Server
```python
@mcp.tool
def process_csv(filepath: str, operation: str) -> dict:
    """Process CSV file with specified operation."""
    import pandas as pd
    
    try:
        df = pd.read_csv(filepath)
        
        if operation == "summary":
            result = df.describe().to_html()
        elif operation == "head":
            result = df.head().to_html()
        else:
            return {"error": "Unknown operation"}
        
        return {
            "content": f"Processed {filepath} with {operation}",
            "custom_html": f"<div>{result}</div>",
            "rows": len(df)
        }
    except Exception as e:
        return {"error": str(e)}
```

### 2. API Integration Server
```python
import requests

@mcp.tool
def fetch_weather(city: str) -> dict:
    """Fetch weather information for a city."""
    try:
        # Replace with actual weather API
        response = requests.get(f"https://api.weather.com/{city}")
        data = response.json()
        
        return {
            "content": f"Weather for {city}: {data['condition']}",
            "temperature": data["temperature"],
            "condition": data["condition"]
        }
    except Exception as e:
        return {"error": str(e)}
```

### 3. System Information Server
```python
import psutil
import platform

@mcp.tool
def system_info() -> dict:
    """Get system information."""
    info = {
        "platform": platform.system(),
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent
    }
    
    html = f"""
    <div class="system-info">
        <h3>System Information</h3>
        <p>Platform: {info['platform']}</p>
        <p>CPU Usage: {info['cpu_percent']}%</p>
        <p>Memory Usage: {info['memory_percent']}%</p>
        <p>Disk Usage: {info['disk_usage']}%</p>
    </div>
    """
    
    return {
        "content": "System information retrieved",
        "custom_html": html,
        **info
    }
```

## Best Practices

1. **Always include error handling** in your tools
2. **Validate all inputs** for security
3. **Return both `content` and structured data** when appropriate
4. **Use `custom_html` for rich displays**
5. **Keep tool descriptions clear and helpful**
6. **Test tools independently** before integration
7. **Follow the naming convention**: folder name must match server name
8. **Don't use underscores** in folder/server names (use camelCase)

## Troubleshooting

### Common Issues

1. **Server not appearing in UI**:
   - Check `mcp.json` configuration
   - Verify user group permissions
   - Ensure server name matches folder name

2. **Tool execution fails**:
   - Check server logs
   - Verify tool parameters
   - Test tool independently

3. **Import errors**:
   - Ensure all dependencies are installed with `uv pip install`
   - Check Python path and virtual environment

4. **Authorization errors**:
   - Verify user groups in `mcp.json`
   - Check `backend/auth.py` for group assignments