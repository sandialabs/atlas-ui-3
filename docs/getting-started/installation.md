# Installation

Last updated: 2026-10-04

This guide provides everything you need to get Atlas UI 3 running, whether you prefer using Docker for a quick setup or setting up a local development environment.

## Quick Start with Docker (Recommended)

Using Docker is the fastest way to get the application running.

#### Generate deployment secrets first

The container has no default for `MCP_TOKEN_ENCRYPTION_KEY`, and Atlas refuses to
start without it, so generate one before your first `docker run` and reuse the
same value on every subsequent run — rotating it invalidates all stored MCP tokens:

```bash
export MCP_TOKEN_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
export CAPABILITY_TOKEN_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
export PROXY_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
```

Store these independent secrets securely and reuse them across restarts and
workers. Configure your authenticating reverse proxy to inject `PROXY_SECRET`
as `X-Proxy-Secret` and a verified user identity; strip client-supplied
copies of both headers. Debug authentication bypasses are off by default.
Without authenticated proxy requests, protected endpoints reject access; an
unauthenticated `/api/heartbeat` response only proves the server is running.
See [authentication configuration](../admin/authentication.md).

### Option 1: Use Pre-built Image from Quay.io

```bash
docker pull quay.io/agarlan-snl/atlas-ui-3:latest
docker run -p 127.0.0.1:8000:8000 \
  -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
  -e CAPABILITY_TOKEN_SECRET="$CAPABILITY_TOKEN_SECRET" \
  -e PROXY_SECRET="$PROXY_SECRET" \
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
    docker run -p 127.0.0.1:8000:8000 \
      -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
      -e CAPABILITY_TOKEN_SECRET="$CAPABILITY_TOKEN_SECRET" \
      -e PROXY_SECRET="$PROXY_SECRET" \
      atlas-ui-3
    ```

3.  **Access the Application:**
    Access the application through your configured authenticating proxy.

### Option 3: Legacy Runtime-only Build Entry Point

The canonical `Dockerfile` is now the multi-stage runtime image published to
both registries. It runs as UID/GID `10001:10001` and does not ship `sudo`,
Node.js, npm, pip, or the repository's test/docs/scripts trees. Python and
bundled MCP runtime dependencies remain available. A healthcheck probes
`/api/heartbeat`; it is a liveness check, not an authentication test.

`Dockerfile.runtimeonly` is a compatibility entry point that requires an
explicit prebuilt image rather than maintaining a second application recipe:

```bash
docker build -t atlas-runtime:local .
docker build --build-arg ATLAS_RUNTIME_IMAGE=atlas-runtime:local \
  -f Dockerfile.runtimeonly -t atlas-ui-3-runtime .
docker run -p 127.0.0.1:8000:8000 \
  -e MCP_TOKEN_ENCRYPTION_KEY="$MCP_TOKEN_ENCRYPTION_KEY" \
  -e CAPABILITY_TOKEN_SECRET="$CAPABILITY_TOKEN_SECRET" \
  -e PROXY_SECRET="$PROXY_SECRET" \
  atlas-ui-3-runtime
```

Build stages use digest-pinned images; update the pins deliberately when
applying base-image security updates. `.dockerignore` excludes local secrets,
configuration, databases, frontend dependencies, and generated assets. Mount
operator configuration at runtime rather than expecting it to be copied from
your working tree. Tools needing Node.js or additional system packages require
a separately maintained derivative image or an external MCP service.

### Option 4: Docker Compose

`docker-compose.yml` is a **development-only** stack, not a production
deployment template. Supply the secrets above and storage credentials in your
environment or a protected `.env` file next to it. Compose refuses to start
when required credentials are missing:

```bash
export MINIO_ROOT_USER=atlas-local
export MINIO_ROOT_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(32))")
export POSTGRES_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(32))")
docker compose up
```

All published ports bind to `127.0.0.1`: Atlas on 8000, MinIO on 9000/9001,
and PostgreSQL on 5432. The MinIO initializer makes `atlas-files` private,
including revoking the old anonymous-download policy on an existing bucket.
Keep storage credentials stable for existing volumes. Changing environment
variables alone does not rotate an existing PostgreSQL database password.

The `atlas-data`, `atlas-logs`, and `minio-data` named volumes preserve DuckDB
history, logs, and uploaded files across container recreation. The image runs
as UID/GID `10001:10001`, so Docker-managed volumes are used instead of host
bind mounts for writable state; mount `./config` for operator overrides. Use
separately managed storage, backups, and least-privilege credentials in
production. Do not enable debug mode to bypass proxy setup in this container:
it listens on a non-loopback address inside its network.

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
*   **`CAPABILITY_TOKEN_SECRET`**: Generate an independent random secret with `openssl rand -hex 32`. Known placeholders and nonempty values shorter than 32 UTF-8 bytes are rejected. `atlas-init` generates both secrets automatically.
*   **Local debug opt-in**: Set `DEBUG_MODE=true`, `ENVIRONMENT=development`, and `ATLAS_HOST=127.0.0.1` only for trusted local development. Debug is no longer enabled by copying the template. Production or non-loopback debug startup is rejected unless the dangerous `ALLOW_DEBUG_NON_LOOPBACK=true` override is explicitly set; see [development authentication](../admin/authentication.md#development-behavior).
*   **`APP_LOG_DIR`**: It is essential to set `APP_LOG_DIR=/workspaces/atlas-ui-3/logs` (or another appropriate path) to ensure application logs are correctly stored. A path inside the checkout is fine for local development -- the test suite overrides this variable with a temp directory, so it cannot pollute test runs (see [test isolation](../developer/test-isolation.md)).
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
