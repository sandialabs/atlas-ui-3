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

#### 4. UI Components

**Header (`frontend/src/components/Header.jsx`)**:
- **Compliance level indicator**: Always visible when a compliance level is selected
- Shows compliance level with Shield icon in a blue badge
- Includes quick clear button (×) to remove the filter
- Ensures users always know their current compliance setting

**ToolsPanel (`frontend/src/components/ToolsPanel.jsx`)**:
- **Filter dropdown**: Allows users to select a compliance level (All Levels, Public, SOC2, etc.)
- **Server badges**: Display compliance level badge on each server entry
- Uses Shield icon from lucide-react

**RagPanel (`frontend/src/components/RagPanel.jsx`)**:
- **Filter dropdown**: Same compliance level filtering as ToolsPanel
- Synced with global compliance level state
- Helps prevent mixing data from different compliance environments

#### 5. Auto-Cleanup Logic (`frontend/src/contexts/ChatContext.jsx`)

Added `setComplianceLevelFilterWithCleanup` wrapper that:
- Automatically clears selected tools that don't match the new compliance level
- Automatically clears selected prompts that don't match the new compliance level
- Prevents users from accidentally using non-compliant tools stored in browser localStorage

```javascript
const setComplianceLevelFilterWithCleanup = useCallback((newLevel) => {
  if (newLevel && newLevel !== selections.complianceLevelFilter) {
    // Clear incompatible tools and prompts
    // ... cleanup logic
  }
  selections.setComplianceLevelFilter(newLevel)
}, [selections, selectedTools, selectedPrompts, config.tools, config.prompts])
```

UI Example:
```
┌─ Header ──────────────────────────────┐
│  [New Chat]  [🔒 SOC2 ×]  [⚙️]  [?]   │
└───────────────────────────────────────┘

┌─ Tools & Integrations ────────────────┐
│  Compliance Level: [SOC2 ▼]           │
├───────────────────────────────────────┤
│  📋 Calculator         [🔒 Public]    │
│  📋 PDF Analyzer       [🔒 SOC2]      │
└───────────────────────────────────────┘

┌─ Data Sources ────────────────────────┐
│  Compliance Level: [SOC2 ▼]           │
│  ☐ Only RAG                           │
├───────────────────────────────────────┤
│  📊 Corporate Cars     [🔒 SOC2]      │
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

**Setting Compliance Level:**
1. Open the Tools panel OR RAG panel in the chat interface
2. Look for the "Compliance Level" dropdown in the controls section
3. Select a compliance level (e.g., "SOC2")
4. The selected level will appear in the header as a blue badge with a Shield icon
5. Only tools and data sources matching that compliance level will be available

**Viewing Active Compliance Level:**
- The header always shows the active compliance level (if one is selected)
- Click the "×" in the header badge to quickly clear the compliance filter

**Important Safety Features:**
- When switching compliance levels, any selected tools/prompts that don't match are **automatically cleared**
- This prevents accidentally running non-compliant tools from previous sessions
- The filter applies across both Tools and RAG Data Sources panels

**Example Workflow:**
1. User selects "SOC2" compliance level from Tools panel
2. Header shows: `[🔒 SOC2 ×]`
3. Only SOC2-compliant tools and data sources are visible
4. Previously selected "Public" tools are automatically unselected
5. User can work safely knowing all interactions are SOC2-compliant

## Benefits

1. **Data Segregation**: Prevents accidental mixing of data from different security environments
2. **Compliance Enforcement**: Helps ensure users only interact with appropriately certified systems
3. **Transparency**: Users can see the compliance level of each tool/data source at a glance
4. **Flexibility**: Supports custom compliance levels beyond standard certifications
5. **Safety**: Auto-cleanup prevents accidental use of non-compliant tools from browser storage
6. **Visibility**: Always-visible header indicator shows current compliance setting

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
