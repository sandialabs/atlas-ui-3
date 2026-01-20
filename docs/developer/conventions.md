# Development Conventions

Last updated: 2026-01-19

To ensure code quality and consistency, please adhere to the following conventions.

*   **Python Package Manager**: **Always** use `uv`. Do not use `pip` or `conda` directly for package management.
*   **Frontend Development**: **Never** use `npm run dev`. It has known WebSocket connectivity issues. Always use `npm run build` to create a production build that the backend will serve.
*   **Backend Development**: **Never** use `uvicorn --reload`. This can cause unexpected issues. Restart the server manually (`python main.py`) to apply changes.
*   **File Naming**: Avoid generic names like `utils.py` or `helpers.py`. Use descriptive names that clearly indicate the file's purpose (e.g., `mcp_tool_manager.py`).
*   **Linting**: Before committing, run the linters to check for style issues:
    *   **Python**: `ruff check backend/`
    *   **Frontend**: `cd frontend && npm run lint`
