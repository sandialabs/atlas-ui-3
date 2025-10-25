# Chat UI Development Instructions

**ALWAYS follow these instructions first and fallback to additional search and context gathering only if the information in these instructions is incomplete or found to be in error.**

## Working Effectively

### Prerequisites and Setup
- **CRITICAL**: Install `uv` Python package manager - this project requires `uv`, NOT pip or conda:
  ```bash
  # Install uv (required)
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # OR as fallback: pip install uv
  uv --version  # Verify installation
  ```
- **Node.js 18+** and npm for frontend development
- **Python 3.12+** for backend

### Initial Environment Setup
```bash
# Create Python virtual environment (takes ~1 second)
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install Python dependencies (takes ~2-3 minutes)
uv pip install -r requirements.txt

# Setup environment configuration
cp .env.example .env
# Edit .env and set DEBUG_MODE=true for development

# Create required directories
mkdir -p logs
```

### Build Process
```bash
# Install frontend dependencies (takes ~15 seconds)
cd frontend
npm install

# Build frontend (takes ~5 seconds)
# CRITICAL: Use npm run build, NOT npm run dev (WebSocket issues)
npm run build

# Verify build output exists
ls -la dist/  # Should contain index.html and assets/
```

### Running the Application
```bash
# Start backend (Terminal 1)
cd backend
python main.py
# Server starts on http://localhost:8000
# NEVER use uvicorn --reload (causes problems)

# Frontend is already built and served by backend
# Open http://localhost:8000 to access application
```

## Testing

### Run All Tests (NEVER CANCEL - takes up to 2 minutes total)
```bash
# Backend tests (takes ~5 seconds) - NEVER CANCEL, set timeout 60+ seconds
./test/run_tests.sh backend

# Frontend tests (takes ~6 seconds) - NEVER CANCEL, set timeout 60+ seconds  
./test/run_tests.sh frontend

# E2E tests (may fail if auth not configured, takes ~70 seconds) - NEVER CANCEL, set timeout 120+ seconds
./test/run_tests.sh e2e

# All tests together - NEVER CANCEL, set timeout 180+ seconds
./test/run_tests.sh all
```

### Code Quality and Linting
```bash
# Python linting (install ruff first if not available)
source .venv/bin/activate
uv pip install ruff
ruff check backend/  # Takes ~1 second

# Frontend linting (takes ~1 second)
cd frontend
npm run lint
```

## Validation Scenarios

### Manual Application Testing
After making changes, ALWAYS validate by running through these scenarios:

1. **Basic Application Load**:
   ```bash
   # Test homepage loads
   curl -s http://localhost:8000/ | grep "Chat UI"
   
   # Test API responds
   curl -s http://localhost:8000/api/config | jq .app_name
   ```

2. **Chat Interface Testing**:
   - Open http://localhost:8000 in browser
   - Verify page loads without console errors
   - Test sending a simple message: "Hello, how are you?"
   - Verify WebSocket connection works (real-time response)
   - Test settings panel opens without errors

3. **MCP Tools Testing** (if enabled):
   - Open settings panel
   - Verify MCP servers are discovered
   - Test a simple tool like calculator: "What's 2+2?"

## Important Development Notes

### Critical Restrictions
- **NEVER use `uvicorn --reload`** - it causes development problems
- **NEVER use `npm run dev`** - it has WebSocket connection issues  
- **ALWAYS use `npm run build`** for frontend development
- **ALWAYS use `uv`** for Python package management, not pip
- **NEVER CANCEL builds or tests** - they may take time but must complete

### Key File Locations
- **Backend**: `/backend/` - FastAPI application with WebSocket support
- **Frontend**: `/frontend/` - React + Vite application  
- **Build Output**: `/frontend/dist/` - served by backend
- **Configuration**: `.env` file (copy from `.env.example`)
- **Tests**: `/test/` directory with individual test scripts
- **Documentation**: `/docs/` directory

### Project Architecture
- **Backend**: FastAPI serving both API and static frontend files
- **Frontend**: React 19 with Vite, Tailwind CSS, builds to `dist/`
- **Communication**: WebSocket for real-time chat, REST API for configuration
- **MCP Integration**: Model Context Protocol for extensible tool support
- **Authentication**: Configurable, set `DEBUG_MODE=true` to skip for development

### Common Issues and Solutions

1. **"uv not found"**: Install uv package manager (most common issue)
2. **WebSocket connection fails**: Use `npm run build` instead of `npm run dev`
3. **Backend won't start**: Check `.env` file exists and `APP_LOG_DIR` path is valid
4. **Frontend not loading**: Ensure `npm run build` completed successfully
5. **Tests failing**: Ensure all dependencies installed and environment configured

### Performance Expectations
- **Python venv creation**: ~1 second
- **Python dependencies**: ~2-3 minutes  
- **Frontend dependencies**: ~15 seconds
- **Frontend build**: ~5 seconds
- **Backend tests**: ~5 seconds
- **Frontend tests**: ~6 seconds
- **E2E tests**: ~70 seconds (may fail without proper auth config)
- **Python linting**: ~1 second
- **Frontend linting**: ~1 second

### Container Development
The project supports containerized development:
```bash
# Build test container (may take 5-10 minutes first time)
docker build -f Dockerfile-test -t atlas-ui-3-test .

# Run tests in container - NEVER CANCEL, set timeout 300+ seconds
docker run --rm atlas-ui-3-test bash /app/test/run_tests.sh all
```

**Note**: Docker builds may fail in some environments due to SSL certificate issues with package repositories. If Docker builds fail, use the local development approach instead.

## Validation Workflow

Before committing changes:
1. **Build**: Ensure both frontend and backend build successfully
2. **Test**: Run test suite - `./test/run_tests.sh all` 
3. **Lint**: Run both Python and frontend linting
4. **Manual**: Test key application scenarios in browser
5. **Exercise**: Test specific functionality you modified

**ALWAYS** set appropriate timeouts for long-running operations and NEVER cancel builds or tests prematurely.