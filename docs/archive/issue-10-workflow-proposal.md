# Proposal: User-Defined Workflows (Issue #10)

**Disclaimer:** This document outlines a *proposal* for implementing a user-defined workflow system. The details are open for discussion and refinement.

## 1. High-Level Concept

The core idea is to empower users to create their own automated processes by chaining together existing MCP (Multi-Capability Provider) functions. These workflows will be represented as a Directed Acyclic Graph (DAG), where each node is a call to an MCP function.

These workflow definitions will be stored as simple JSON objects, making them easy to create, edit, and manage through a new UI in the frontend. The backend will be responsible for interpreting these JSON definitions and executing the MCP functions in the correct order, passing data between them.

## 2. Workflow Definition

Workflows would be defined in a JSON structure. This format is both human-readable and easy for the backend to parse. Each workflow would have:

*   A `name` and `description`.
*   A list of `nodes`, where each node represents an MCP function to be executed.
*   Each `node` would have a unique `id`, the `function_name` to call from the available MCPs, a dictionary of `inputs` for that function, and a list of `dependencies` (the `id`s of other nodes that must complete before this one can run).

### Example Workflow JSON

```json
{
  "name": "Read and Process File",
  "description": "A simple workflow to read a file and then execute some python code.",
  "nodes": [
    {
      "id": "read_file_node",
      "function_name": "filesystem.read_file",
      "inputs": {
        "path": "/path/to/your/file.txt"
      },
      "dependencies": []
    },
    {
      "id": "process_data_node",
      "function_name": "code-executor.run_python",
      "inputs": {
        "code": "print(f'''File content was read in previous step''')"
      },
      "dependencies": ["read_file_node"]
    }
  ]
}
```
*Note: The input to the second node demonstrates how the output of a previous node could be referenced.*

## 3. Storage

Given the existing use of MinIO, the most straightforward approach would be to store these workflow JSON files in a dedicated MinIO bucket (e.g., a new bucket named `workflows`). This leverages the existing infrastructure and provides a simple and scalable storage solution.

## 4. Backend Implementation

The backend would require a few new components:

*   **API Endpoints:** A new set of RESTful endpoints would be created (e.g., under `/api/workflows`). These would handle the CRUD (Create, Read, Update, Delete) operations for workflows, plus an endpoint to trigger an execution.
    *   `GET /workflows` - List all workflows.
    *   `POST /workflows` - Create a new workflow.
    *   `GET /workflows/{id}` - Retrieve a single workflow.
    *   `PUT /workflows/{id}` - Update a workflow.
    *   `DELETE /workflows/{id}` - Delete a workflow.
    *   `POST /workflows/{id}/execute` - Start a new execution of the workflow.
*   **Workflow Service:** A new service layer would contain the business logic for managing and executing workflows. It would interact with MinIO for storage.
*   **Execution Engine:** This would be the core component. When a workflow execution is triggered, the engine would:
    1.  Fetch the workflow JSON from MinIO.
    2.  Construct a graph object from the nodes and dependencies.
    3.  Perform a topological sort on the graph to determine the correct execution order. The `networkx` library is a lightweight and powerful option for this.
    4.  Execute each MCP function sequentially, handling the inputs and outputs of each node. The output of a node could be made available as input to subsequent nodes.

## 5. Frontend Implementation

The frontend would be updated to include a new "Workflows" section.

*   **Workflow Management UI:**
    *   A main view to list all existing workflows, with options to edit, delete, or execute them.
    *   A creation/editing form. For a first version, this could be a simple form with a text area for pasting or editing the workflow JSON directly.
*   **Graphical Editor (Recommended):**
    *   For a more user-friendly experience, a graphical, drag-and-drop editor for building workflows is highly recommended. **React Flow** is a popular, well-documented, and powerful library for this purpose. It would allow users to visually connect nodes and build their DAG.
*   **Execution Monitoring:**
    *   When a workflow is executed, the UI could show its status (running, completed, failed) and display the final output.

## Conclusion

This approach provides a flexible and powerful way for users to define their own automated processes by composing the existing MCP functions. It builds on the current architecture and can be implemented iteratively, starting with a simple JSON editor and expanding to a more advanced graphical interface over time.
