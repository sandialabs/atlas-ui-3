# Frontend API Endpoints

This document outlines the API endpoints expected by the frontend of the application.

## WebSocket Communication

The frontend establishes a WebSocket connection to the backend for real-time communication.

*   **Endpoint**: `/ws`

The WebSocket connection is used for sending and receiving various types of messages, including chat messages, agent updates, and file-related events.

### Outgoing Messages (Client to Server)

*   **`chat`**: Sends a chat message to the backend.
    ```json
    {
      "type": "chat",
      "content": "Hello, world!",
      "model": "gpt-4",
      "selected_tools": [],
      "selected_prompts": [],
      "selected_data_sources": [],
      "only_rag": false,
      "tool_choice_required": false,
      "user": "user@example.com",
      "files": {},
      "agent_mode": false,
      "agent_max_steps": 10
    }
    ```
*   **`download_file`**: Requests a file download from the backend.
    ```json
    {
      "type": "download_file",
      "filename": "example.txt"
    }
    ```

### Incoming Messages (Server to Client)

*   **`chat_response`**: A standard response from the assistant.
*   **`error`**: An error message from the backend.
*   **`agent_step_update`**: An update on the current step of a running agent.
*   **`agent_final_response`**: The final response from an agent.
*   **`intermediate_update`**: A container for various update types, including `tool_call`, `tool_result`, `canvas_content`, `canvas_files`, `custom_ui`, `files_update`, and `file_download`.
*   **`agent_update`**: A container for agent-related updates, including `agent_start`, `agent_turn_start`, `agent_tool_call`, `agent_completion`, `agent_error`, and `agent_max_steps`.

## HTTP Endpoints

The frontend also communicates with the backend using traditional HTTP requests for various purposes.

### Admin Endpoints

*   **`GET /admin/logs/viewer`**: Fetches logs from the backend.
*   **`POST /admin/logs/clear`**: Clears the logs on the backend.
*   **`GET /admin/system-status`**: Fetches the system status.
*   **`GET /admin/`**: Fetches the admin dashboard data.
*   **`GET /admin/llm-config`**: Fetches the LLM configuration.
*   **`GET /admin/help-config`**: Fetches the help configuration.
*   **`POST /admin/trigger-health-check`**: Triggers a health check of the system.
*   **`GET /admin/mcp-health`**: Fetches the health status of the MCP.
*   **`POST /admin/reload-config`**: Reloads the configuration on the backend.
*   **`GET /admin/mcp-config`**: Fetches the MCP configuration.
*   **`GET /admin/banners`**: Fetches the banner messages.

### API Endpoints

*   **`GET /api/config`**: Fetches the application configuration.
*   **`GET /api/files`**: Fetches the list of files for the current user.
*   **`GET /api/users/{userEmail}/files/stats`**: Fetches file statistics for a user.
*   **`DELETE /api/files/{fileKey}`**: Deletes a file.
*   **`POST /api/feedback`**: Submits feedback.
*   **`GET /api/files/{s3_key}`**: Fetches a file from S3.
*   **`GET /api/files/download/{s3_key}`**: Downloads a file from S3.
*   **`GET /api/banners`**: Fetches the banner messages.
