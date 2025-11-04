# Quick Start Guide

Get the Chat UI project running quickly with these simple steps.

## Using Docker (Recommended for Quick Setup)

1. **Build the container**:
   ```bash
   docker build -t atlas-ui-3 .
   ```

2. **Run the container**:
   ```bash
   docker run -p 8000:8000 atlas-ui-3
   ```

3. **Access the interface**:
   Open http://localhost:8000 in your browser

## Local Development Setup

### Prerequisites
- **Python 3.12+**
- **Node.js 18+** (for frontend development)
- **uv** (Python package manager - see installation below)

### Install uv (Python Package Manager)
```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh
# or on Windows:
# powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Important**: This project uses **uv** as the Python package manager, not pip or conda.

### 1. Set up Python environment
```bash
cd atlas-ui-3-11
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your API keys and settings
```

### 3. Build the frontend
```bash
cd frontend
npm install
npm run build
```

### 4. Start the backend
```bash
cd ../backend
python main.py
```

### 5. Access the interface
Open http://localhost:8000 in your browser

## Configuration Basics

- **`.env`** - Environment variables (API keys, debug mode, etc.)
- **`llmconfig.yml`** - LLM model configurations
- **`mcp.json`** - MCP server configurations

See [Configuration Guide](../dev-docs/configuration.md) for detailed setup.

## Next Steps

- [Developer Setup](../dev-docs/developer-setup.md) - Full development environment
- [Backend Guide](../dev-docs/backend.md) - Backend architecture and development
- [Frontend Guide](../dev-docs/frontend.md) - Frontend development
- [MCP Development](../dev-docs/mcp-development.md) - Creating MCP servers