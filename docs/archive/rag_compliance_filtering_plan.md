### Plan: Implement RAG Data Source Compliance Filtering

This document outlines the plan to implement compliance-level filtering for RAG (Retrieval-Augmented Generation) data sources based on a user's selected compliance level.

#### 1. Update RAG Service Methods

*   **Modify `RAGMCPService.discover_servers`:**
    *   Change the method signature in `backend/domain/rag_mcp_service.py` from `discover_servers(self, username: str)` to `discover_servers(self, username: str, user_compliance_level: Optional[str] = None)`.
    *   This new parameter will carry the compliance level selected by the user in the UI.

*   **Modify `RAGMCPService.discover_data_sources`:**
    *   Change the method signature in `backend/domain/rag_mcp_service.py` from `discover_data_sources(self, username: str)` to `discover_data_sources(self, username: str, user_compliance_level: Optional[str] = None)`.
    *   This new parameter will also carry the compliance level selected by the user.

#### 2. Implement Server Filtering

*   **Location:** Inside both `RAGMCPService.discover_servers` and `RAGMCPService.discover_data_sources` in `backend/domain/rag_mcp_service.py`.
*   **Logic:** Before processing each RAG server (i.e., before calling `self.mcp_manager.call_tool` for discovery), retrieve the server's configured `compliance_level`.
*   **Access Check:** Obtain an instance of `ComplianceLevelManager` using `get_compliance_manager()` from `backend/core/compliance.py`.
*   **Filtering:** Use `compliance_mgr.is_accessible(user_level=user_compliance_level, resource_level=server_compliance_level)` to determine if the user is authorized to access the server.
*   **Action:** If `is_accessible` returns `False`, skip the current server entirely, preventing its data sources from being discovered or returned.

#### 3. Implement Data Source Filtering

*   **Location:** Within `RAGMCPService.discover_servers` in `backend/domain/rag_mcp_service.py`, specifically after retrieving the `resources` (individual data sources) from an accessible server.
*   **Logic:** Iterate through each `r` (resource/data source) in the `resources` list. Retrieve the data source's `complianceLevel`.
*   **Access Check:** Use `compliance_mgr.is_accessible(user_level=user_compliance_level, resource_level=data_source_compliance_level)`.
*   **Action:** If `is_accessible` returns `False` for a specific data source, remove that data source from the `ui_sources` list for that server.

#### 4. Update API Endpoint

*   **Location:** The `get_config` function in `backend/routes/config_routes.py`, which handles the `/api/config` endpoint.
*   **Parameter Addition:** Modify the `get_config` function signature to accept a new optional query parameter, e.g., `compliance_level: Optional[str] = None`. This parameter will capture the user's compliance level selection from the frontend.

#### 5. Plumb Compliance Level to Services

*   **Location:** Inside the `get_config` function in `backend/routes/config_routes.py`.
*   **Action:** When calling `rag_mcp.discover_data_sources()` and `rag_mcp.discover_servers()`, pass the `compliance_level` received from the API request as the `user_compliance_level` argument to these service methods.

This plan ensures that the user's selected compliance level is propagated through the system and used to filter both RAG servers and individual RAG data sources, providing a secure and compliant data access experience.
