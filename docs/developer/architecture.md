# Architecture Overview

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
    *   `ChatContext`: Manages the state of the chat, including messages and user selections.
    *   `WSContext`: Manages the WebSocket connection.
*   **Styling**: Uses Tailwind CSS for utility-first styling.
