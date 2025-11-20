# Refactoring Recommendations for `backend/core`

I have thoroughly reviewed the files in your `backend/core` directory and have the following recommendations for refactoring them into more structured, less coupled modules, similar to your existing `backend/modules` structure. This approach will enhance maintainability, testability, and scalability.

**General Principles for Refactoring:**
*   **Single Responsibility Principle:** Each module should have one clear, well-defined purpose.
*   **Loose Coupling:** Modules should interact through well-defined interfaces, minimizing direct dependencies.
*   **Cohesion:** Related functionalities should be grouped together within the same module.

**Specific Refactoring Recommendations:**

1.  **`backend/modules/auth`**
    *   **Purpose:** Centralize all authentication and authorization logic.
    *   **Contents to Move:**
        *   `backend/core/auth.py` (functions like `is_user_in_group`, `get_user_from_header`).
        *   `backend/core/auth_utils.py` (the `AuthorizationManager` class and `create_authorization_manager` function).
        *   `validate_selected_tools` from `backend/core/utils.py` (as it's heavily tied to authorization and tool access).

2.  **`backend/modules/callbacks`**
    *   **Purpose:** Centralize all callback definitions and their management.
    *   **Contents to Move:**
        *   `backend/core/callbacks.py` (general session callbacks).
        *   `backend/core/custom_callbacks.py` (example/advanced session callbacks).
    *   **Consideration:** If a `CallbackManager` class is needed to register and trigger callbacks, it should also reside here.

3.  **`backend/modules/chat`**
    *   **Purpose:** Encapsulate the core chat session logic and its lifecycle.
    *   **Contents to Move:**
        *   `backend/core/chat_session.py` (the current, simpler `ChatSession` class).
    *   **Consideration:** This module would depend on the new `backend/modules/callbacks` for triggering session events.

4.  **`backend/modules/config`** (Leverage existing module)
    *   **Purpose:** Centralize application configuration management and retrieval.
    *   **Contents to Move/Integrate:**
        *   `backend/core/config_routes.py` (API routes for retrieving configuration information for the frontend) should be moved into this module, perhaps as `config_api.py`.
        *   Helper functions for reading/writing configuration files (like `get_file_content`, `write_file_content`, `setup_configfilesadmin` from `admin_routes.py` if they are specific to config files) could be extracted into a `config_utils.py` within this module.

5.  **`backend/modules/feedback`**
    *   **Purpose:** Encapsulate all logic related to user feedback submission and management.
    *   **Contents to Move:**
        *   `backend/core/feedback_routes.py` (API routes for feedback) should be moved into this module, perhaps as `feedback_api.py`.
        *   Related Pydantic models (`FeedbackData`, `FeedbackResponse`) and helper functions (`get_feedback_directory`).

6.  **`backend/modules/file_handling`**
    *   **Purpose:** Define policies and logic for how files are processed, especially regarding their exposure to LLM context versus tool-only access.
    *   **Contents to Move:**
        *   `backend/core/file_config.py` (`FilePolicy` class and file filtering functions).

7.  **`backend/modules/file_storage`** (Leverage existing module)
    *   **Purpose:** Encapsulate all logic related to file storage operations, particularly with S3.
    *   **Contents to Move:**
        *   `backend/core/files_routes.py` (API routes for S3 file management) should be moved into this module, perhaps as `file_storage_api.py`.
        *   Related Pydantic models (`FileUploadRequest`, `FileResponse`).

8.  **`backend/modules/http`**
    *   **Purpose:** Provide a centralized and standardized way to make HTTP requests with consistent error handling and logging.
    *   **Contents to Move:**
        *   `backend/core/http_client.py` (the `UnifiedHTTPClient` class and custom exception classes).

9.  **`backend/modules/middleware`**
    *   **Purpose:** Centralize FastAPI middleware definitions.
    *   **Contents to Move:**
        *   `backend/core/middleware.py` (the `AuthMiddleware` class).

10. **`backend/modules/observability`**
    *   **Purpose:** Centralize all logic related to application observability, including structured logging and OpenTelemetry tracing.
    *   **Contents to Move:**
        *   `backend/core/otel_config.py` (the `OpenTelemetryConfig` class and `JSONFormatter`).

11. **`backend/modules/prompts`** (Leverage existing module)
    *   **Purpose:** Centralize all logic related to managing and loading system prompts and other prompt-related utilities.
    *   **Contents to Move:**
        *   `backend/core/prompt_utils.py` (functions like `load_system_prompt`, `get_system_prompt_path`).

12. **`backend/modules/llm`** (Leverage existing module)
    *   **Purpose:** Centralize LLM interaction and tool execution logic.
    *   **Contents to Move:**
        *   `call_llm` and `call_llm_with_tools` from `backend/core/utils.py`.
        *   `create_agent_completion_tool` from `backend/core/utils.py`.

13. **`backend/modules/admin`** (New Module)
    *   **Purpose:** Encapsulate admin-specific API routes and related functionalities.
    *   **Contents to Move:**
        *   `backend/core/admin_routes.py` (the `admin_router` and associated functions).

14. **`backend/core/orchestrator.py`**
    *   **Recommendation:** This file already acts as a coordinator for the `backend/modules`. It could remain in `backend/core` as a high-level component, or be moved directly to the `backend/` root directory if `core` is intended to be completely empty after refactoring. Its current role aligns with the goal of reducing coupling.

15. **`backend/core/utils.py`**
    *   **Recommendation:** After moving the LLM/tool-related functions and `validate_selected_tools`, only `get_current_user` would remain. If this is the only function, it should be moved to `backend/modules/auth` (as it's related to user context) or a new `backend/modules/api_utils` if a general API utility module is created. The goal should be to empty `backend/core/utils.py` if possible.

16. **`backend/core/old_session.py`**
    *   **Recommendation:** This file appears to be a deprecated version of `chat_session.py` with more extensive functionality. If it is no longer in use, it should be removed to avoid confusion and maintain a clean codebase.

These recommendations aim to create a more modular, maintainable, and scalable backend architecture by clearly defining responsibilities and reducing inter-module coupling.
