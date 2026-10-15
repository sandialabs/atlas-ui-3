# Installation

Last updated: 2026-10-12

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

`Dockerfile.runtimeonly` is a compatibility entry point that reuses an
already-built image rather than maintaining a second application recipe. It
defaults to the published `quay.io/agarlan-snl/atlas-ui-3:latest` tag; pass
`--build-arg ATLAS_RUNTIME_IMAGE=...` to point it at a locally built image (the
shim copies nothing from your working tree, so build the canonical `Dockerfile`
first if you want local source):

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
deployment template. The base stack starts only the Atlas application against
your own object storage and DuckDB chat history. Bundled MinIO and PostgreSQL
are opt-in through additional Compose files, and Compose only asks for the
credentials of services you actually include (issue #1052): an external-S3
deployment never needs `MINIO_ROOT_PASSWORD`, and DuckDB never needs
`POSTGRES_PASSWORD`. Supply the secrets above and storage credentials in your
environment or a protected `.env` file next to the Compose files. Compose
refuses to start when a required credential is missing or empty.

To store files in the bundled MinIO, include its file. MinIO's root account
then uses the same `S3_ACCESS_KEY` / `S3_SECRET_KEY` pair, so the stack has
exactly one object-storage credential (MinIO requires that user to be at
least 3 and the password at least 8 characters long):

```bash
export S3_ENDPOINT=http://minio:9000
export S3_ACCESS_KEY=atlas-local
export S3_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
export PROXY_SECRET="$PROXY_SECRET"   # from the "Generate deployment secrets first" step
docker compose -f docker-compose.yml -f docker-compose.minio.yml up -d
```

To store files in your own S3-compatible endpoint instead, run the base stack
alone and point `S3_ENDPOINT` at it:

```bash
export S3_ENDPOINT=https://s3.example.com
export S3_ACCESS_KEY=...   # your access key
export S3_SECRET_KEY=...   # your secret key
export PROXY_SECRET="$PROXY_SECRET"
docker compose up -d
```

To store chat history in the bundled PostgreSQL instead of DuckDB, include its
file and set `POSTGRES_PASSWORD` — the stack builds `CHAT_HISTORY_DB_URL`
from it automatically (keep the password URL-safe: letters, digits, and
`-._~`):

```bash
export POSTGRES_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(32))")
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

To use an external database with the Compose stack, add your own override
file that sets `CHAT_HISTORY_DB_URL` (the `.env` value is for host-run Atlas;
the container only accepts its own absolute `/data` DuckDB path or an
operator-provided override). `S3_ENDPOINT` may hold any value in `.env` for
host-run Atlas; the bundled MinIO file overrides it inside the stack. Tip:
`export COMPOSE_FILE=docker-compose.yml:docker-compose.minio.yml:docker-compose.postgres.yml`
selects the bundled stack for every subsequent `docker compose` command in
the current shell.

#### Upgrading from the single-file stack

Stacks started before this change need new settings before they start again:
the base stack no longer knows about `MINIO_ROOT_USER` /
`MINIO_ROOT_PASSWORD` or a bundled `postgres` service, so Compose now fails
configuration with a clear message until the variables below are set.

- Plain `docker compose up` now starts only Atlas. Add
  `-f docker-compose.minio.yml` (and/or `-f docker-compose.postgres.yml`) to
  keep using the bundled services.
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` are no longer read; Compose now
  demands the application's `S3_ACCESS_KEY` / `S3_SECRET_KEY` pair instead.
  Set both to freshly generated values — object data is independent of the
  root credential, so migrated files stay readable without the old
  `minioadmin`/`minioadmin` pair (which is publicly known; do not reuse it).
- Stacks that ran before v0.8.0 kept files in the host bind mount
  `./data/minio`, which the bundled MinIO file does not mount (it uses the
  `minio-data` named volume). Copy them in before first use, with the
  ownership the server's uid 1001 expects:

  ```bash
  docker volume create <project>_minio-data
  docker run --rm -u root \
    -v <project>_minio-data:/target -v "$PWD/data/minio":/src:ro \
    docker.io/bitnamilegacy/minio:2025.7.23-debian-12-r5@sha256:6dabb4a2088c9a79908de3bc05f4586c23ad2182c8908e7e3acbf61c1467fb20 \
    sh -c 'cp -a /src/. /target/ && chown -R 1001:1001 /target'
  ```

- The server stores data under `/bitnami/minio/data`. A leftover
  `minio-data` named volume from a v0.8.0-era stack holds nothing (that
  configuration could not start the server). If the new stack reports a
  permission error on it, remove just that volume with
  `docker volume rm <project>_minio-data` (find the exact name with
  `docker volume ls`). Do NOT use `docker compose down -v` for this: it
  deletes every volume of the project, including `atlas-data` (chat history
  and stored MCP tokens) and `atlas-logs`.
- `CHAT_HISTORY_DB_URL` from your `.env` no longer reaches the container;
  the bundled PostgreSQL file supplies its own URL from `POSTGRES_PASSWORD`.

`DEBUG_MODE` is no longer set, so proxy-secret enforcement is active: the stack
rejects any request that does not carry the `X-Proxy-Secret` header. Reaching
the UI therefore requires an authenticating reverse proxy in front of Atlas
that injects `X-Proxy-Secret` and a verified identity header, and strips
client-supplied copies (an nginx example is in
[authentication configuration](../admin/authentication.md#proxy-secret-authentication-enabled-by-default)).
A bare `http://127.0.0.1:8000` hit only proves the container is up — the
heartbeat healthcheck does not test authentication.

All published ports bind to `127.0.0.1`: Atlas on 8000, MinIO on 9000/9001
when the bundled MinIO file is included, and PostgreSQL on 5432 when the
bundled PostgreSQL file is included. The MinIO initializer makes `atlas-files`
private, including revoking the old anonymous-download policy on an existing
bucket. Keep storage credentials stable for existing volumes. Changing
environment variables alone does not rotate an existing PostgreSQL database
password.

The `atlas-data` and `atlas-logs` named volumes (plus `minio-data` when the
bundled MinIO file is included) preserve DuckDB history, logs, uploaded files,
and per-user MCP tokens (`/data/tokens`) across container recreation. The
image runs as UID/GID `10001:10001`, so Docker-managed volumes are used
instead of host bind mounts for writable state; mount `./config` for operator
overrides. Because that bind mount is host-owned, the nonroot UID cannot write
it: `chown -R 10001:10001 config` before `docker compose up` if you use the
admin MCP editor, which writes `mcp.json` there. Use separately managed
storage, backups, and least-privilege credentials in production. Do not enable
debug mode to bypass proxy setup in this container: it listens on a
non-loopback address inside its network.

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
*   **Local debug opt-in**: Set `DEBUG_MODE=true`, `ENVIRONMENT=development`, and `ATLAS_HOST=127.0.0.1` only for trusted local development. Debug is no longer enabled by copying the template. Production or non-loopback debug startup is rejected unless the matching dangerous override is explicitly set — `ALLOW_DEBUG_PRODUCTION=true` for a production environment, `ALLOW_DEBUG_NON_LOOPBACK=true` for a non-loopback bind; the two are independent. See [development authentication](../admin/authentication.md#development-behavior).
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
