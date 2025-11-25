# Installation

This guide provides everything you need to get Atlas UI 3 running, whether you prefer using Docker for a quick setup or setting up a local development environment.

## Quick Start with Docker (Recommended)

Using Docker is the fastest way to get the application running.

1.  **Build the Docker Image:**
    From the root of the project, run the build command:
    ```bash
    docker build -t atlas-ui-3 .
    ```

2.  **Run the Container:**
    Once the image is built, start the container:
    ```bash
    docker run -p 8000:8000 atlas-ui-3
    ```

3.  **Access the Application:**
    Open your web browser and navigate to [http://localhost:8000](http://localhost:8000).

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

# Install Python dependencies
uv pip install -r requirements.txt
```

### 3. Configure Your Environment

Copy the example `.env` file to create your local configuration.

```bash
cp .env.example .env
```

Now, open the `.env` file and add your API keys for the LLM providers you intend to use (e.g., `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

**Important Configuration Notes:**
*   **`APP_LOG_DIR`**: It is essential to set `APP_LOG_DIR=/workspaces/atlas-ui-3/logs` (or another appropriate path) to ensure application logs are correctly stored.
*   **`USE_MOCK_S3`**: For local development and personal use, setting `USE_MOCK_S3=true` is acceptable. However, **this must never be used in a production environment** due to security and data durability concerns.

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

After running the script, the application will be available at `http://localhost:8000`.

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
cd backend
python main.py
```

The backend will be available at `http://localhost:8000`.

## Next Steps

With the application running, you can now explore its features. For more detailed information on configuration and administration, refer to the [Administrator's Guide](../admin/README.md). If you plan to contribute, the [Developer's Guide](../developer/README.md) provides in-depth architectural details.
