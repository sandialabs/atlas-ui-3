# Developer Setup

Comprehensive guide for setting up a development environment.

## Development Environment Requirements

- **Python 3.12+**
- **Node.js 18+** and npm
- **uv** (Python package manager)

## Installing uv (Critical!)

**This project uses `uv` as the Python package manager**, not pip or conda. Many developers miss this step.

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows:
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify installation
uv --version
```

## Development Cycle

### 1. Setup Python environment (from root directory)
```bash
cd /path/to/atlas-ui-3-11
uv venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
uv pip install -r requirements.txt
```

### 2. Start backend (Terminal 1)
```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
# Note: No --reload flag to avoid auto-reload issues
```

### 3. Build frontend (Terminal 2)
```bash
cd frontend
npm install
npm run build
```

## Important Development Notes

- **Use `uv`** for all Python package management, not pip
- **Don't use `uvicorn --reload`** - it causes problems
- **Don't use `npm run dev`** - it has WebSocket issues
- **Use `npm run build`** instead for production build
- Backend serves on port 8000, frontend builds to `/dist`
- The built frontend files are served by the FastAPI backend, so you only need the uvicorn server running

## Project Structure

```
├── frontend/          # React frontend application
│   ├── dist/          # Built frontend files (served by backend)
│   ├── src/           # React source code
│   │   ├── components/ # React components
│   │   ├── contexts/   # React contexts
│   │   └── hooks/      # Custom React hooks
│   ├── package.json   # Node.js dependencies
│   └── vite.config.js # Vite build configuration
├── atlas/           # FastAPI backend (MCP client)
│   ├── main.py        # Main FastAPI application
│   ├── session.py     # WebSocket session management
│   ├── message_processor.py # Core message processing logic
│   ├── config.py      # Unified Pydantic configuration system
│   ├── mcp/           # MCP servers
│   └── logs/          # Application logs
├── .env               # Environment variables
├── llmconfig.yml      # LLM configurations
└── mcp.json           # MCP server configurations
```

## Development Tools

### Code Quality
- **Ruff**: Python linting and formatting
- **ESLint**: JavaScript linting (frontend)

### Running linters
```bash
# Python (from root directory)
ruff check atlas/
ruff format atlas/

# Frontend
cd frontend
npm run lint
```

## Testing the Application

1. **Build the frontend first**:
   ```bash
   cd frontend
   npm run build
   ```

2. **Start the backend**:
   ```bash
   cd ../backend
   python main.py
   ```

3. **Test the interface**:
   - Open http://localhost:8000
   - Test WebSocket connection with a simple message
   - Verify MCP tools are available (if configured)

## Common Development Issues

1. **WebSocket connection fails**: Check if backend is running on correct port
2. **Authentication errors**: Verify `x-email-header` or enable `DEBUG_MODE=true` in `.env`
3. **MCP tools not available**: Check user group permissions in `mcp.json`
4. **Frontend not loading**: Ensure `npm run build` was successful
5. **"uv not found"**: Install uv package manager (this is the most common issue!)

## Adding New Features

### Backend Development
- Follow the 400-line file limit
- Use the unified configuration system in `config.py`
- Use `http_client.UnifiedHTTPClient` for HTTP requests
- Use `auth_utils.AuthorizationManager` for permission checks
- Always use `exc_info=True` for error logging

### Frontend Development
- Built with React and Vite
- Uses Tailwind CSS for styling
- WebSocket integration through React contexts
- Components organized by functionality

### Adding New MCP Servers
1. Create new directory in `atlas/mcp/`
2. Implement `main.py` with MCP protocol
3. Add configuration to `mcp.json`
4. Set appropriate user groups

## Environment Configuration

Create and edit your `.env` file:
```bash
cp .env.example .env
```

Key variables:
- `DEBUG_MODE=true` - Skip authentication in development
- `MOCK_RAG=true` - Use mock RAG service for testing
- `OPENAI_API_KEY` - Your OpenAI API key
- `ANTHROPIC_API_KEY` - Your Anthropic API key

See [Configuration Guide](configuration.md) for complete details.

## Debugging Tips

- Use browser developer tools to inspect WebSocket messages
- Check `atlas/logs/app.log` for server-side errors (includes full tracebacks)
- Test configuration loading: `python -c "from config import config_manager; print('✅ Config OK')"`
- Test MCP servers independently using command line