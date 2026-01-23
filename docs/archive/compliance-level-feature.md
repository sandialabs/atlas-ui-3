# Compliance Level Feature

## Overview
This feature allows MCP servers, RAG data sources, and LLM endpoints to declare a compliance level (e.g., SOC2, HIPAA, Public, External, Internal). Users can then filter their session to only connect to and use sources matching a specific compliance level. This helps minimize the risk of mixing data from secure and insecure environments.

The feature includes:
- **Standardized compliance levels** defined in `compliance-levels.json`
- **Explicit allowlist model** where each level defines which other levels can be used together
- **Validation and normalization** of compliance level names with warnings for invalid values
- **Rollout control flag** (`FEATURE_COMPLIANCE_LEVELS_ENABLED`) for gradual deployment

## Compliance Level Definitions

Compliance levels are defined in `config/defaults/compliance-levels.json` (can be overridden in `config/overrides/compliance-levels.json`):

```json
{
  "version": "2.0",
  "description": "Defines compliance level types and their allowed combinations",
  "levels": [
    {
      "name": "Public",
      "description": "Publicly accessible data, no restrictions",
      "aliases": [],
      "allowed_with": ["Public"]
    },
    {
      "name": "External",
      "description": "External services with basic enterprise security",
      "aliases": [],
      "allowed_with": ["External"]
    },
    {
      "name": "Internal",
      "description": "Internal systems, can handle company IP information",
      "aliases": [],
      "allowed_with": ["Internal"]
    },
    {
      "name": "SOC2",
      "description": "SOC 2 Type II compliant systems",
      "aliases": ["SOC-2", "SOC 2"],
      "allowed_with": ["SOC2"]
    },
    {
      "name": "HIPAA",
      "description": "HIPAA compliant systems for healthcare data",
      "aliases": ["HIPAA-Compliant"],
      "allowed_with": ["HIPAA", "SOC2"]
    },
    {
      "name": "FedRAMP",
      "description": "FedRAMP authorized systems for government data",
      "aliases": ["FedRAMP-Moderate", "FedRAMP-High"],
      "allowed_with": ["FedRAMP", "SOC2"]
    }
  ],
  "mode": "explicit_allowlist",
  "mode_description": "Each compliance level explicitly defines which other levels can be used in the same session."
}
```

### Explicit Allowlist Model

Each compliance level has an `allowed_with` array that explicitly defines which compliance levels can be used together in the same session:

- **Public** sessions can use: Public resources only
- **External** sessions can use: External resources only
- **Internal** sessions can use: Internal resources only
- **SOC2** sessions can use: SOC2 resources only
- **HIPAA** sessions can use: HIPAA and SOC2 resources
- **FedRAMP** sessions can use: FedRAMP and SOC2 resources

### Why Explicit Allowlist?

The allowlist model prevents dangerous resource combinations:

**Problem with hierarchical model:**
- If HIPAA (high security) could access Public (low security) resources
- A HIPAA session could use public internet search tools
- Search queries containing patient PII could leak to public services

**Solution with allowlist:**
- HIPAA explicitly allows only HIPAA and SOC2
- Public internet search tools are excluded
- Prevents accidental PII leakage

Administrators have full control over which combinations are safe for their environment by editing the `allowed_with` arrays.

Resources without a compliance_level are **hidden** when a compliance filter is active (strict filtering mode). This prevents untagged Public resources from appearing in SOC2 or HIPAA sessions.

## Feature Flag

**Environment Variable:** `FEATURE_COMPLIANCE_LEVELS_ENABLED`

**Default:** `false` (disabled)

**Purpose:** 
- Enable/disable compliance level filtering across the entire application
- Allows for controlled rollout to specific environments
- When disabled, all compliance UI elements are hidden and filtering is bypassed

**To Enable:**
```bash
# .env file
FEATURE_COMPLIANCE_LEVELS_ENABLED=true
```

## Implementation

### Backend Changes

#### 1. Compliance Level Manager (`backend/core/compliance.py`)
New module that manages compliance level definitions:
- Loads compliance levels from `compliance-levels.json`
- Validates compliance level names against defined options
- Normalizes aliases to canonical names (e.g., "SOC 2" → "SOC2")
- Implements hierarchical access checking
- Logs warnings for invalid compliance levels

Key methods:
```python
compliance_mgr = get_compliance_manager()

# Validate and normalize a compliance level
canonical = compliance_mgr.validate_compliance_level("SOC 2", context="for LLM model 'gpt-4'")
# Returns: "SOC2" (canonical name)

# Check if a resource is accessible
is_ok = compliance_mgr.is_accessible("SOC2", "Internal")  
# Returns: True (SOC2 level 3 can access Internal level 2)

# Get all accessible levels for a user
levels = compliance_mgr.get_accessible_levels("SOC2")
# Returns: {"Public", "External", "Internal", "SOC2"}
```

#### 2. Configuration Models (`backend/modules/config/manager.py`)
Added `compliance_level` field to `MCPServerConfig`:

```python
class MCPServerConfig(BaseModel):
    # ... existing fields ...
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "SOC2", "HIPAA", "Public")
```

Added `compliance_level` field to `ModelConfig` for LLM endpoints:

```python
class ModelConfig(BaseModel):
    # ... existing fields ...
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "External", "Internal", "Public")
```

**Validation on Config Load:**
When configurations are loaded, compliance levels are automatically validated:
- Invalid levels trigger warning logs
- Aliases are normalized to canonical names
- Invalid levels are set to `None` to prevent errors

Example warning log:
```
WARNING: Invalid compliance level 'SOCII' for MCP server 'pdfbasic'. 
Valid levels: Public, External, Internal, SOC2, HIPAA, FedRAMP. Setting to None.
```

Added feature flag to `AppSettings`:
```python
class AppSettings(BaseSettings):
    # ... existing fields ...
    feature_compliance_levels_enabled: bool = Field(
        False,
        description="Enable compliance level filtering for MCP servers and data sources",
        validation_alias=AliasChoices("FEATURE_COMPLIANCE_LEVELS_ENABLED"),
    )
```

#### 3. Configuration Files
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

**config/defaults/rag-sources.json** and **config/overrides/rag-sources.json**:
```json
{
  "corporate_cars": {
    "type": "mcp",
    "command": ["python", "mcp/corporate_cars/main.py"],
    "cwd": "backend",
    "groups": ["users"],
    "compliance_level": "SOC2"
    // ... other fields
  }
}
```

**config/defaults/llmconfig.yml** and **config/overrides/llmconfig.yml** (LLM endpoints):
```yaml
models:
  gpt-4.1:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-4.1"
    api_key: "${OPENAI_API_KEY}"
    compliance_level: "External"
```

#### 3. API Responses (`backend/routes/config_routes.py`)
The `/api/config` endpoint now includes:
- `compliance_level` in **tools** array (for MCP tool servers)
- `compliance_level` in **prompts** array (for MCP prompt servers)
- `compliance_level` in **models** array (for LLM endpoints)
- `compliance_levels` in **features** object (feature flag status)

**RAG MCP Service** (`backend/domain/rag_mcp_service.py`):
- Added `complianceLevel` to RAG server discovery responses
- RAG sources can inherit compliance level from their server or specify their own

Example API response:
```json
{
  "models": [
    {
      "name": "gpt-4.1",
      "description": null,
      "compliance_level": "External"
    },
    {
      "name": "internal-llm",
      "description": "Internal LLM for sensitive data",
      "compliance_level": "Internal"
    }
  ],
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
  ],
  "features": {
    "compliance_levels": true
  }
}
```

#### 4. Backend Message Handling
The compliance level filter is transmitted to the backend with every chat message:

```javascript
// Frontend sends to backend
{
  type: 'chat',
  content: '...',
  compliance_level_filter: 'SOC2',  // Current compliance filter
  selected_tools: [...],
  // ... other fields
}
```

This enables future backend features:
- **Compliance-based logging**: Different audit requirements per compliance level
- **LLM endpoint selection**: Route to compliance-appropriate LLM endpoints
- **Rate limiting**: Different rate limits per compliance tier
- **Data retention policies**: Compliance-specific data handling

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
Exposed compliance level filter through ChatContext and sends to backend:
```javascript
// Exposed in context
{
  complianceLevelFilter,
  setComplianceLevelFilter
}

// Sent to backend in chat messages
sendMessage({
  type: 'chat',
  compliance_level_filter: selections.complianceLevelFilter,
  // ... other fields
})
```

#### 3. Marketplace Context (`frontend/src/contexts/MarketplaceContext.jsx`)
Added filtering functions with strict compliance checking:
```javascript
// Strict filtering: when compliance level is selected, resources without compliance_level are hidden
const isComplianceAccessible = (userLevel, resourceLevel) => {
  // If user level is not set, all resources are accessible
  if (!userLevel) return true
  
  // STRICT MODE: If user has selected a compliance level but resource has none, deny access
  if (!resourceLevel) return false
  
  // Find user's compliance level object
  const userLevelObj = complianceLevels.find(l => l.name === userLevel)
  
  // If we don't have level info, deny access (strict)
  if (!userLevelObj) return false
  
  // Check if resource level is in the user's allowed_with list
  return userLevelObj.allowed_with && userLevelObj.allowed_with.includes(resourceLevel)
}

const getComplianceFilteredTools = (complianceLevel) => {
  if (!complianceLevel) return getFilteredTools()
  return getFilteredTools().filter(tool => {
    // STRICT MODE: Only show resources with matching compliance levels
    return isComplianceAccessible(complianceLevel, tool.compliance_level)
  })
}
```

**Strict Filtering Mode:**
- When a compliance level is selected, **only** resources with matching compliance levels are shown
- Resources without `compliance_level` are **hidden** when a filter is active
- Prevents untagged Public resources from appearing in SOC2 or HIPAA sessions
- Example: User selects "SOC2" → sees only SOC2 resources (not Public, not untagged MCPs)

#### 4. UI Components

**All UI components are conditional on the `features.compliance_levels` flag:**

**Header (`frontend/src/components/Header.jsx`)** - Single source of compliance level selection:
- **Compliance level dropdown**: Centralized dropdown for selecting compliance level for the entire session
- Displays in header with Shield icon
- All tools, RAG sources, and LLM endpoints automatically filter based on selected level
- Dropdown shows available compliance levels (Public, External, Internal, SOC2, HIPAA, FedRAMP)
- "All Levels" option to disable filtering
- Hidden when `FEATURE_COMPLIANCE_LEVELS_ENABLED=false`
- **Model dropdown**: Filters LLM endpoints by compliance level
- **Model badges**: Display compliance level badge on each model option

**ToolsPanel (`frontend/src/components/ToolsPanel.jsx`)**:
- **Server badges**: Display compliance level badge on each server entry
- Tools automatically filtered by compliance level selected in Header
- Uses Shield icon from lucide-react
- Badges hidden when feature disabled

**RagPanel (`frontend/src/components/RagPanel.jsx`)**:
- Data sources automatically filtered by compliance level selected in Header
- Prevents mixing data from different compliance environments
- Hidden when feature disabled

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

UI Example (when feature enabled):
```
┌─ Header ──────────────────────────────────────────────┐
│  [New Chat]  [🔒 Compliance: SOC2 ▼]  [⚙️]  [?]      │
└───────────────────────────────────────────────────────┘

┌─ Tools & Integrations ────────────────────┐
│  [Add from Marketplace]  [Clear All]      │
├───────────────────────────────────────────┤
│  📋 Calculator         [🔒 Public]        │  ← Hidden in SOC2 session
│  📋 PDF Analyzer       [🔒 SOC2]          │  ← Shown in SOC2 session
└───────────────────────────────────────────┘

┌─ Data Sources ────────────────────────────┐
│  ☐ Only RAG                               │
├───────────────────────────────────────────┤
│  📊 Corporate Cars     [🔒 SOC2]          │  ← Shown in SOC2 session
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

4. For LLM endpoints in `llmconfig.yml`:
   ```yaml
   models:
     gpt-4.1:
       model_url: "https://api.openai.com/v1/chat/completions"
       model_name: "gpt-4.1"
       api_key: "${OPENAI_API_KEY}"
       compliance_level: "External"
   ```

5. Supported values are arbitrary strings, but common examples include:
   - **For MCP/RAG**: `"Public"`, `"SOC2"`, `"HIPAA"`, `"FedRAMP"`
   - **For LLM endpoints**: `"External"`, `"Internal"`, `"Public"`
   - Or any custom compliance level

### For End Users

**Setting Compliance Level:**
1. Open the Tools panel OR RAG panel in the chat interface
2. Look for the "Compliance Level" dropdown in the controls section
3. Select a compliance level (e.g., "SOC2", "External", "Internal")
4. The selected level will appear in the header as a blue badge with a Shield icon
5. Only matching tools, data sources, and LLM models will be available

**Viewing Active Compliance Level:**
- The header always shows the active compliance level (if one is selected)
- Click the "×" in the header badge to quickly clear the compliance filter

**How It Affects Different Components:**
- **LLM Models**: Model dropdown only shows models matching the compliance level (with badges)
- **MCP Tools**: Tools panel only shows servers matching the compliance level
- **RAG Sources**: Data sources panel only shows sources matching the compliance level

**Important Safety Features:**
- When switching compliance levels, any selected tools/prompts that don't match are **automatically cleared**
- This prevents accidentally running non-compliant tools from previous sessions
- The filter applies across Tools, RAG Data Sources, and LLM model selection

**Example Workflow:**
1. User selects "External" compliance level from Tools panel
2. Header shows: `[🔒 External ×]`
3. Only External-compliant LLMs, tools, and data sources are visible
4. Previously selected "Internal" LLM model is automatically unselected
5. User can work safely knowing all interactions use external-compliant resources

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
