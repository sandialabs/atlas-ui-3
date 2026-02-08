# Architecture Overview

Last updated: 2026-02-08

The application is composed of a React frontend and a FastAPI backend, communicating via WebSockets.

## Backend

The backend follows a clean architecture pattern, separating concerns into distinct layers:

*   **`domain`**: Contains the core business logic and data models, with no dependencies on frameworks or external services.
*   **`application`**: Orchestrates the business logic from the domain layer to perform application-specific use cases.
*   **`infrastructure`**: Handles communication with external systems like databases, web APIs, and the file system. It's where adapters for external services are implemented.
*   **`interfaces`**: Defines the contracts (protocols) that the different layers use to communicate, promoting loose coupling.
*   **`routes`**: Defines the HTTP API endpoints.

## Frontend

The frontend is a modern React 19 application built with Vite.

*   **State Management**: Uses React's Context API for managing global state. There is no Redux.
    *   `ChatContext`: Manages the state of the chat, including messages and user selections. Validates persisted selections (tools, prompts, data sources) against the live `/api/config` response and removes stale entries automatically.
    *   `WSContext`: Manages the WebSocket connection.
    *   `MarketplaceContext`: Manages MCP server discovery and marketplace selections. Prunes servers that no longer exist in the backend config.
*   **Styling**: Uses Tailwind CSS for utility-first styling.
