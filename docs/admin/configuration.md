# Configuration Architecture

The application uses a layered configuration system that loads settings from three primary sources in the following order of precedence:

1.  **Environment Variables (`.env`)**: Highest priority. These override any settings from files.
2.  **Override Files (`config/overrides/`)**: For custom, instance-specific configurations. These files are not checked into version control.
3.  **Default Files (`config/defaults/`)**: The base configuration that is part of the repository.

**Note**: The definitive source for all possible configuration options and their default values is the `AppSettings` class within `backend/modules/config/config_manager.py`. This class dictates how the application reads and interprets all its settings.

## Key Override Files

To customize your instance, you will place your own versions of the configuration files in the `config/overrides/` directory. The most common files to override are:

*   **`mcp.json`**: Registers and configures the MCP (tool) servers that provide capabilities to the LLM.
*   **`llmconfig.yml`**: Defines the list of available Large Language Models and their connection details.
*   **`compliance-levels.json`**: Defines the security compliance levels (e.g., Public, Internal, HIPAA) and the rules for how they can interact.
*   **`help-config.json`**: Populates the content of the "Help" modal in the user interface.
*   **`splash-config.json`**: Configures the startup splash screen for displaying policies and information to users.
*   **`messages.txt`**: Defines the text for system-wide banner messages that can be displayed to all users.

## The `.env` File

This file is crucial for setting up your instance. Start by copying the example file:

```bash
cp .env.example .env
```

Key settings in the `.env` file include:

*   **API Keys**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.
*   **Authentication Header**: `AUTH_USER_HEADER` configures the HTTP header name used to extract the authenticated username from your reverse proxy (default: `X-User-Email`).
*   **Feature Flags**: Enable or disable major features like `FEATURE_AGENT_MODE_AVAILABLE`.
*   **S3 Connection**: Configure the connection to your S3-compatible storage. For local testing, you can set `USE_MOCK_S3=true` to use an in-memory mock instead of a real S3 bucket. **This mock must never be used in production.**
*   **Log Directory**: The `APP_LOG_DIR` variable points to the folder where the application log file (`app.jsonl`) will be stored. This path must be updated to a valid directory in your deployment environment.
