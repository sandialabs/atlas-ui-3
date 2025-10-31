# Compliance Level Feature

## Overview
This feature allows MCP servers and RAG data sources to declare a compliance level (e.g., SOC2, HIPAA, Public). Users can then filter their session to only connect to and use sources matching a specific compliance level. This helps minimize the risk of mixing data from secure and insecure environments.

## Implementation

### Backend Changes

#### 1. Configuration Model (`backend/modules/config/manager.py`)
Added `compliance_level` field to `MCPServerConfig`:

```python
class MCPServerConfig(BaseModel):
    # ... existing fields ...
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "SOC2", "HIPAA", "Public")
```

#### 2. Configuration Files
Updated MCP server configurations to include compliance levels:

**config/defaults/mcp.json** and **config/overrides/mcp.json**:
```json
{
  "calculator": {
    "command": ["python", "mcp/calculator/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "compliance_level": "Public"
    // ... other fields
  },
  "pdfbasic": {
    "command": ["python", "mcp/pdfbasic/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "compliance_level": "SOC2"
    // ... other fields
  }
}
```

**config/defaults/mcp-rag.json** and **config/overrides/mcp-rag.json**:
```json
{
  "corporate_cars": {
    "command": ["python", "mcp/corporate_cars/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "compliance_level": "SOC2"
    // ... other fields
  }
}
```

#### 3. API Responses (`backend/routes/config_routes.py`)
The `/api/config` endpoint now includes `compliance_level` in:
- **tools** array (for MCP tool servers)
- **prompts** array (for MCP prompt servers)

**RAG MCP Service** (`backend/domain/rag_mcp_service.py`):
- Added `complianceLevel` to RAG server discovery responses
- RAG sources can inherit compliance level from their server or specify their own

Example API response:
```json
{
  "tools": [
    {
      "server": "calculator",
      "tools": ["calculate"],
      "compliance_level": "Public"
    },
    {
      "server": "pdfbasic",
      "tools": ["analyze_pdf"],
      "compliance_level": "SOC2"
    }
  ],
  "rag_servers": [
    {
      "server": "corporate_cars",
      "complianceLevel": "SOC2",
      "sources": [
        {
          "id": "q3_sales_forecast",
          "name": "Q3 Sales Forecast",
          "complianceLevel": "SOC2"
        }
      ]
    }
  ]
}
```

### Frontend Changes

#### 1. State Management (`frontend/src/hooks/chat/useSelections.js`)
Added compliance level filter to user selection state:
```javascript
const [complianceLevelFilter, setComplianceLevelFilter] = usePersistentState(
  'chatui-compliance-level-filter', 
  null
)
```

#### 2. Context (`frontend/src/contexts/ChatContext.jsx`)
Exposed compliance level filter through ChatContext:
```javascript
{
  complianceLevelFilter,
  setComplianceLevelFilter
}
```

#### 3. Marketplace Context (`frontend/src/contexts/MarketplaceContext.jsx`)
Added filtering functions for compliance levels:
```javascript
const getComplianceFilteredTools = (complianceLevel) => {
  if (!complianceLevel) return getFilteredTools()
  return getFilteredTools().filter(tool => {
    if (!tool.compliance_level) return true  // Backward compatible
    return tool.compliance_level === complianceLevel
  })
}
```

#### 4. UI Components (`frontend/src/components/ToolsPanel.jsx`)
Added compliance level filter dropdown and badges:
- **Filter dropdown**: Allows users to select a compliance level (All Levels, Public, SOC2, etc.)
- **Server badges**: Display compliance level badge on each server entry
- Uses Shield icon from lucide-react

UI Example:
```
┌─ Tools & Integrations ────────────────┐
│  Compliance Level: [SOC2 ▼]           │
├───────────────────────────────────────┤
│  📋 Calculator         [🔒 Public]    │
│  📋 PDF Analyzer       [🔒 SOC2]      │
└───────────────────────────────────────┘
```

## Usage

### For Administrators
1. Edit MCP server configuration files (`config/defaults/mcp.json` or `config/overrides/mcp.json`)
2. Add `compliance_level` field to each server:
   ```json
   {
     "server_name": {
       "command": [...],
       "compliance_level": "SOC2"
     }
   }
   ```
3. Supported values are arbitrary strings, but common examples include:
   - `"Public"` - Publicly available, no special compliance
   - `"SOC2"` - SOC 2 Type II compliant
   - `"HIPAA"` - HIPAA compliant
   - `"FedRAMP"` - FedRAMP authorized
   - Or any custom compliance level

### For End Users
1. Open the Tools panel in the chat interface
2. Look for the "Compliance Level" dropdown in the controls section
3. Select a compliance level (e.g., "SOC2")
4. Only tools and data sources matching that compliance level will be shown
5. Select "All Levels" to see all available tools regardless of compliance level

## Benefits

1. **Data Segregation**: Prevents accidental mixing of data from different security environments
2. **Compliance Enforcement**: Helps ensure users only interact with appropriately certified systems
3. **Transparency**: Users can see the compliance level of each tool/data source
4. **Flexibility**: Supports custom compliance levels beyond standard certifications

## Backward Compatibility

- Servers without a `compliance_level` field are shown in all filter modes
- The feature is opt-in; existing configurations continue to work without modification
- Frontend gracefully handles null/missing compliance levels

## Testing

### Backend Tests
Located in `backend/tests/test_compliance_level.py`:
- Tests MCPServerConfig with and without compliance_level
- Tests MCPConfig parsing with compliance levels
- Verifies compliance_level is serialized correctly

Run tests:
```bash
pytest backend/tests/test_compliance_level.py
```

### Manual Testing
1. Start the application: `cd backend && python main.py`
2. Open http://localhost:8000
3. Open the Tools panel
4. Verify compliance level dropdown appears with available levels
5. Select a compliance level and verify tools are filtered
6. Verify badges show on tool servers

## Example Configuration

### Complete MCP Server Configuration
```json
{
  "secure_database": {
    "command": ["python", "mcp/secure_db/main.py"],
    "cwd": "backend",
    "groups": ["finance"],
    "is_exclusive": false,
    "description": "Access to financial database",
    "author": "Security Team",
    "short_description": "Financial DB access",
    "help_email": "security@example.com",
    "compliance_level": "SOC2"
  },
  "public_api": {
    "command": ["python", "mcp/public_api/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "is_exclusive": false,
    "description": "Public API for general data",
    "author": "Engineering Team",
    "short_description": "Public API",
    "help_email": "engineering@example.com",
    "compliance_level": "Public"
  }
}
```

## Future Enhancements

Potential improvements for future iterations:
1. Hierarchical compliance levels (e.g., HIPAA implies SOC2)
2. Multiple compliance levels per server
3. Visual indicators beyond badges (colors, icons)
4. Compliance level warnings/confirmations
5. Audit logging of compliance-filtered sessions
