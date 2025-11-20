# Decoupling Tool Execution with a Message Broker

This document outlines a plan to refactor the application's tool execution workflow by introducing a message broker (RabbitMQ). The goal is to decouple long-running tool calls from the main web server process, making the UI more responsive and the backend more scalable and resilient.

## Analysis of the Current Synchronous Workflow

The current implementation processes tool calls synchronously within the same `asyncio.task` that handles the WebSocket connection. This creates a bottleneck.

1.  **Entry Point**: A `chat` message arrives at the `websocket_endpoint` in `main.py`.
2.  **Orchestration**: The request flows from `ChatService` to `ChatOrchestrator`, which determines the execution mode. For tools, it invokes `ToolsModeRunner.run`.
3.  **Tool Logic**: `ToolsModeRunner.run` in `modes/tools.py` calls the LLM to determine which tools to use, then calls `await tool_utils.execute_tools_workflow(...)`.
4.  **The Synchronous Block**: The `execute_tools_workflow` function calls `execute_single_tool` for each tool. This is the critical blocking section:
    *   **Approval Wait**: The function sends an approval request to the UI and then `await`s an `asyncio.Future` from the `ToolApprovalManager`. This pauses the entire execution chain until the user responds.
    *   **Execution Wait**: Immediately after approval, it `await`s the result of `tool_manager.execute_tool(...)`, which is the potentially long-running function call itself.

This design means the web server process is tied up waiting for both user interaction and the completion of slow I/O tasks, reducing its availability to handle other requests.

---

## Detailed Decoupling Plan

This plan introduces RabbitMQ to offload the execution part of the tool call while keeping the necessary synchronous approval step in the main application thread.

### Step 1: Introduce Infrastructure (Job Queue & Connection Manager)

We will add two new infrastructure components to manage messaging and WebSocket connections.

1.  **Add Dependency**: The `aio-pika` library will be added to `pyproject.toml` to handle asynchronous communication with RabbitMQ.
2.  **New File: `backend/infrastructure/transport/connection_manager.py`**
    *   This file will define a singleton `ConnectionManager` class.
    *   It will maintain a dictionary `active_connections: Dict[UUID, WebSocket]` to map session IDs to active WebSocket objects.
    *   It will provide methods: `connect(session_id, websocket)`, `disconnect(session_id)`, and `get_connection(session_id)`.
3.  **New File: `backend/infrastructure/queue_client.py`**
    *   This file will define a `QueueClient` class to abstract RabbitMQ operations.
    *   It will manage connections, channels, and declare two queues: `tool_execution_queue` for jobs and `tool_results_queue` for results.

### Step 2: Create the Asynchronous Worker

A new, standalone Python process will be responsible for executing the tools.

*   **New File: `backend/worker.py`**
    *   This script will initialize the `QueueClient`.
    *   It will define a consumer function that listens to the `tool_execution_queue`.
    *   When a job is received, the consumer will:
        1.  Deserialize the job payload (containing the tool call details and session context).
        2.  Call a new, refactored function `tool_utils.execute_approved_tool(...)` to perform the actual work.
        3.  Orchestrate the final LLM synthesis call to get the user-facing response.
        4.  Publish the final result, including the `session_id`, to the `tool_results_queue`.

### Step 3: Modify the Core Logic to Enqueue Jobs

We will change the application to hand off jobs to the worker instead of executing them directly.

1.  **Modify `backend/application/chat/utilities/tool_utils.py`**:
    *   The `execute_single_tool` function will be refactored. The logic for the approval flow will remain.
    *   After receiving user approval, instead of executing the tool, the function will return the approved `tool_call` object and its arguments.
    *   A **new function, `execute_approved_tool`**, will be created containing the logic that was previously *after* the approval block (i.e., calling `tool_manager.execute_tool`). This is the function the worker will import and call.

2.  **Modify `backend/application/chat/modes/tools.py`**:
    *   In `ToolsModeRunner.run`, the call to `tool_utils.execute_tools_workflow` will still be `await`ed to handle the synchronous approval step.
    *   This function will now return a list of *approved* tool calls.
    *   For each approved tool call, `ToolsModeRunner` will create a job payload and use the `QueueClient` to publish it to the `tool_execution_queue`.
    *   It will then immediately send a message to the UI (e.g., "Processing tools...") and return, freeing up the server process.

### Step 4: Route Results Back to the UI

The main application needs to listen for results from the worker and push them to the correct user.

1.  **Modify `backend/main.py`**:
    *   The `websocket_endpoint` will be updated to use the `ConnectionManager` to register connections on connect and deregister them on disconnect.
    *   In the `lifespan` context manager, a background `asyncio.task` will be started to run a `results_consumer` function.
    *   This `results_consumer` will listen to the `tool_results_queue`.
    *   When a result message arrives, it will use the `ConnectionManager` to look up the correct WebSocket by `session_id` and send the final response to the user.

---
## Summary of Changes

### New Files
- `backend/worker.py`
- `backend/infrastructure/queue_client.py`
- `backend/infrastructure/transport/connection_manager.py`

### Modified Files
- `pyproject.toml` (to add `aio-pika`)
- `backend/main.py`
- `backend/application/chat/modes/tools.py`
- `backend/application/chat/utilities/tool_utils.py`
