# Installation

Last updated: 2026-08-09

This guide provides everything you need to get Atlas UI 3 running, whether you prefer using Docker for a quick setup or setting up a local development environment.

## Quick Start with Docker (Recommended)

Using Docker is the fastest way to get the application running.

#### Generate the required encryption key first

The container has no default for `MCP_TOKEN_ENCRYPTION_KEY`, and Atlas refuses to
start without it, so generate one before your first `docker run` and reuse the
same value on every subsequent run — rotating it invalidates all stored MCP tokens:

```bash
export MCP_TOKEN_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

### Option 1: Use Pre-built Image from Quay.io

```bash
docker pull quay.io/agarlan-snl/atlas-ui-3:latest
docker run -p 8000:8000 \
  -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
  quay.io/agarlan-snl/atlas-ui-3:latest
```

### Option 2: Build Locally

1.  **Build the Docker Image:**
    From the root of the project, run the build command:
    ```bash
    docker build -t atlas-ui-3 .
    ```

2.  **Run the Container:**
    Once the image is built, start the container:
    ```bash
    docker run -p 8000:8000 \
      -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
      atlas-ui-3
    ```

3.  **Access the Application:**
    Open your web browser and navigate to [http://localhost:8000](http://localhost:8000).

### Option 3: Build a Runtime-only Image

Use the runtime-only Dockerfile when you want a slimmer deployed image with only runtime dependencies:

```bash
docker build -f Dockerfile.runtimeonly -t atlas-ui-3-runtime .
docker run -p 8000:8000 \
  -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
  atlas-ui-3-runtime
```

### Option 4: Docker Compose

`docker-compose.yml` requires `MCP_TOKEN_ENCRYPTION_KEY` to be set in the
environment or in a `.env` file next to it, and fails with a clear message if it
is missing:

```bash
echo "MCP_TOKEN_ENCRYPTION_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
docker compose up
```

## Local Development Setup

For those who want to contribute to the code or run the application natively, follow these steps.

### Prerequisites

*   **Python 3.12+**
*   **Node.js 18+** and npm
*   **uv**: This project uses `uv` as the Python package manager. It's required.

### 1. Install `uv`

If you don't have `uv` installed, open your terminal and run the following command. This is a critical step.

```bash
# Install uv on macOS, Linux, or WSL
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows (PowerShell):
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify the installation
uv --version
```

### 2. Set Up the Environment

From the project's root directory, set up the Python virtual environment and install the required packages.

```bash
# Create the virtual environment
uv venv

# Activate the environment
# On macOS, Linux, or WSL:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install atlas package in editable mode (with dev dependencies)
# The mcp-demos extra installs what the bundled demo MCP servers import at
# startup (python-pptx, pandas, matplotlib, ...). Omit it and servers such as
# pptx_generator fail tool discovery with "Connection closed".
uv pip install -e ".[dev,mcp-demos]"
```

### 3. Configure Your Environment

Copy the example `.env` file to create your local configuration.

```bash
cp .env.example .env
```

Now, open the `.env` file and add your API keys for the LLM providers you intend to use (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

**Important Configuration Notes:**
*   **`MCP_TOKEN_ENCRYPTION_KEY`**: You must replace the placeholder that ships in `.env.example`. It is a public value, so Atlas rejects it and refuses to start. Generate your own with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and keep it stable — rotating it invalidates all stored MCP tokens.
*   **`APP_LOG_DIR`**: It is essential to set `APP_LOG_DIR=/workspaces/atlas-ui-3/logs` (or another appropriate path) to ensure application logs are correctly stored.
*   **`USE_MOCK_S3`**: For local development and personal use, setting `USE_MOCK_S3=true` is acceptable. However, **this must never be used in a production environment** due to security and data durability concerns.
*   **`SKIP_AUTHORIZATION_CHECKS`** (optional, local-only convenience): In debug mode the mock authorization table only grants admin access to two hardcoded identities (`ADMIN_TEST_USER`, default `admin@example.com`, and `test@test.com`), so a new contributor running locally with their real email would otherwise have to set `ADMIN_TEST_USER` to match it before reaching admin-gated routes. Setting `SKIP_AUTHORIZATION_CHECKS=true` skips that step -- every group check returns `True`, so any locally authenticated user has full access. **Blast radius is broader than admin pages:** because `is_user_in_group` is the single gate for every group-restricted surface, enabling it also unlocks group-restricted models (`atlas/core/model_access.py`), MCP servers gated by `required_groups` (`mcp_execution.py`), and feedback/capture routes. In debug mode a headerless request is assigned the `test_user` identity, so with this flag on any request reaching the port is effectively an administrator. It is strictly opt-in (commented out in `.env.example`), never affects authentication, and the app refuses to start if the flag is set without `DEBUG_MODE=true`, when `ENVIRONMENT=production`, or together with `AUTH_GROUP_CHECK_URL`. See [docs/admin/authentication.md](../admin/authentication.md) for full guardrail details.

### 4. All-in-One Start Script (Recommended)

For convenience, you can use the `agent_start.sh` script, which automates the process of building the frontend and starting the backend. This is the recommended way to run the application for local development.

```bash
bash agent_start.sh
```

#### Starting with MCP Mock Server

If you want to test MCP functionality during development, you can start the MCP mock server alongside the main application:

```bash
# Start both the main application and MCP mock server
bash agent_start.sh -m

# Other options
bash agent_start.sh -f    # Only rebuild frontend
bash agent_start.sh -b    # Only start backend
```

The MCP mock server will be available at `http://127.0.0.1:8005/mcp` and provides simulated database tools for testing.

After running the script, the application will be available at `http://localhost:8000` (default). Set `PORT` in `.env` to use a different port.

### Manual Setup

If you prefer to run the frontend and backend processes separately, follow these steps.

#### 5. Build the Frontend

The frontend is a React application that needs to be built before the backend can serve it.

```bash
cd frontend
npm install
npm run build
```

**Important:** Always use `npm run build`. Do not use `npm run dev`, as it has known issues with WebSocket connections in this project.

#### 6. Start the Backend

Finally, start the FastAPI backend server.

```bash
cd atlas
PYTHONPATH=.. python main.py
```

The backend will be available at `http://localhost:8000`.

Alternatively, if you installed the package in editable mode (`pip install -e .`), you can use:

```bash
atlas-server --port 8000
```

## Next Steps

With the application running, you can now explore its features. For more detailed information on configuration and administration, refer to the [Administrator's Guide](../admin/README.md). If you plan to contribute, the [Developer's Guide](../developer/README.md) provides in-depth architectural details.
