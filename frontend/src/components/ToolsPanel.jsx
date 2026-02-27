import { X, Trash2, Search, Plus, Wrench, Shield, Info, ChevronDown, ChevronRight, Sparkles, Save, Server, User, Mail, Key, ShieldCheck } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useState, useEffect, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import UnsavedChangesDialog from './UnsavedChangesDialog'
import TokenInputModal from './TokenInputModal'
import { useServerAuthStatus } from '../hooks/useServerAuthStatus'

// Default type for schema properties without explicit type
const DEFAULT_PARAM_TYPE = 'any'

// Truncation message constant for better maintainability
const TRUNCATION_MESSAGE = 'This description has been truncated. Showing start and end of content.'

const ToolsPanel = ({ isOpen, onClose }) => {
  const [searchTerm, setSearchTerm] = useState('')
  const [expandedTools, setExpandedTools] = useState(new Set())
  const [expandedPrompts, setExpandedPrompts] = useState(new Set())
  const [collapsedServers, setCollapsedServers] = useState(new Set())
  const [expandedDescriptions, setExpandedDescriptions] = useState(new Set())
  const navigate = useNavigate()
  const prevOpenRef = useRef(false)
  const {
    selectedTools: savedSelectedTools,
    selectedPrompts: savedSelectedPrompts,
    addTools: saveAddTools,
    removeTools: saveRemoveTools,
    addPrompts: saveAddPrompts,
    removePrompts: saveRemovePrompts,
    toolChoiceRequired: savedToolChoiceRequired,
    setToolChoiceRequired: saveSetToolChoiceRequired,
    clearToolsAndPrompts,
    complianceLevelFilter,
    tools: allTools,
    prompts: allPrompts,
    features
  } = useChat()
  const { getComplianceFilteredTools, getComplianceFilteredPrompts, getFilteredTools, getFilteredPrompts } = useMarketplace()
  
  // Local state for pending changes
  const [pendingSelectedTools, setPendingSelectedTools] = useState(new Set())
  const [pendingSelectedPrompts, setPendingSelectedPrompts] = useState(new Set())
  const [pendingToolChoiceRequired, setPendingToolChoiceRequired] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)
  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false)

  // Auth status state
  const [tokenModalServer, setTokenModalServer] = useState(null)
  const [tokenUploadLoading, setTokenUploadLoading] = useState(false)
  const [tokenUploadError, setTokenUploadError] = useState(null)
  const [disconnectServer, setDisconnectServer] = useState(null)
  const [disconnectError, setDisconnectError] = useState(null)
  const { fetchAuthStatus, uploadToken, removeToken, getServerAuth } = useServerAuthStatus()
  
  // Initialize pending state from saved state only when panel transitions from closed to open
  useEffect(() => {
    if (isOpen && !prevOpenRef.current) {
      setPendingSelectedTools(new Set(savedSelectedTools))
      setPendingSelectedPrompts(new Set(savedSelectedPrompts))
      setPendingToolChoiceRequired(savedToolChoiceRequired)
      setHasChanges(false)
    }
    prevOpenRef.current = isOpen
  }, [isOpen, savedSelectedTools, savedSelectedPrompts, savedToolChoiceRequired])

  // Fetch auth status when panel opens
  useEffect(() => {
    if (isOpen) {
      fetchAuthStatus()
    }
  }, [isOpen, fetchAuthStatus])
  
  // Use pending state while editing
  const selectedTools = pendingSelectedTools
  const selectedPrompts = pendingSelectedPrompts
  const toolChoiceRequired = pendingToolChoiceRequired
  
  // Toggle functions that work with pending state
  const toggleTool = (toolKey) => {
    setPendingSelectedTools(prev => {
      const next = new Set(prev)
      if (next.has(toolKey)) {
        next.delete(toolKey)
      } else {
        next.add(toolKey)
      }
      setHasChanges(true)
      return next
    })
  }
  
  const togglePrompt = (promptKey) => {
    setPendingSelectedPrompts(prev => {
      const next = new Set(prev)
      if (next.has(promptKey)) {
        next.delete(promptKey)
      } else {
        next.add(promptKey)
      }
      setHasChanges(true)
      return next
    })
  }
  
  const addTools = (toolKeys) => {
    setPendingSelectedTools(prev => {
      const next = new Set(prev)
      toolKeys.forEach(k => next.add(k))
      setHasChanges(true)
      return next
    })
  }
  
  const removeTools = (toolKeys) => {
    setPendingSelectedTools(prev => {
      const next = new Set(prev)
      toolKeys.forEach(k => next.delete(k))
      setHasChanges(true)
      return next
    })
  }
  
  const addPrompts = (promptKeys) => {
    setPendingSelectedPrompts(prev => {
      const next = new Set(prev)
      promptKeys.forEach(k => next.add(k))
      setHasChanges(true)
      return next
    })
  }
  
  const removePrompts = (promptKeys) => {
    setPendingSelectedPrompts(prev => {
      const next = new Set(prev)
      promptKeys.forEach(k => next.delete(k))
      setHasChanges(true)
      return next
    })
  }
  
  const setToolChoiceRequired = (value) => {
    setPendingToolChoiceRequired(value)
    setHasChanges(true)
  }
  
  // Save handler - commits pending changes to context
  const handleSave = () => {
    // Determine what tools to add or remove
    const toolsToAdd = Array.from(pendingSelectedTools).filter(t => !savedSelectedTools.has(t))
    const toolsToRemove = Array.from(savedSelectedTools).filter(t => !pendingSelectedTools.has(t))
    
    if (toolsToAdd.length > 0) saveAddTools(toolsToAdd)
    if (toolsToRemove.length > 0) saveRemoveTools(toolsToRemove)
    
    // Determine what prompts to add or remove
    const promptsToAdd = Array.from(pendingSelectedPrompts).filter(p => !savedSelectedPrompts.has(p))
    const promptsToRemove = Array.from(savedSelectedPrompts).filter(p => !pendingSelectedPrompts.has(p))
    
    if (promptsToAdd.length > 0) saveAddPrompts(promptsToAdd)
    if (promptsToRemove.length > 0) saveRemovePrompts(promptsToRemove)
    
    // Update tool choice required if changed
    if (pendingToolChoiceRequired !== savedToolChoiceRequired) {
      saveSetToolChoiceRequired(pendingToolChoiceRequired)
    }
    
    setHasChanges(false)
    onClose()
  }
  
  // Cancel handler - reverts pending changes
  const handleCancel = () => {
    setPendingSelectedTools(new Set(savedSelectedTools))
    setPendingSelectedPrompts(new Set(savedSelectedPrompts))
    setPendingToolChoiceRequired(savedToolChoiceRequired)
    setHasChanges(false)
    onClose()
  }
  
  // Clear all tools and prompts in pending state
  const handleClearAll = () => {
    setPendingSelectedTools(new Set())
    setPendingSelectedPrompts(new Set())
    setHasChanges(true)
  }

  // Handle close attempts - check for unsaved changes
  const handleCloseAttempt = () => {
    if (hasChanges) {
      setShowUnsavedDialog(true)
    } else {
      onClose()
    }
  }

  // Handle confirmation dialog actions
  const handleSaveAndClose = () => {
    handleSave() // This already calls onClose()
    setShowUnsavedDialog(false)
  }

  const handleDiscardAndClose = () => {
    handleCancel() // This already calls onClose()
    setShowUnsavedDialog(false)
  }

  const handleCancelDialog = () => {
    setShowUnsavedDialog(false)
  }

  // Handle token upload for JWT/bearer auth servers
  const handleTokenUpload = async (tokenData) => {
    if (!tokenModalServer) return
    setTokenUploadLoading(true)
    setTokenUploadError(null)
    try {
      await uploadToken(tokenModalServer, tokenData)
      setTokenModalServer(null)
      setTokenUploadError(null)
    } catch (err) {
      console.error('Token upload failed:', err)
      setTokenUploadError(err.message || 'Failed to save token. Please try again.')
    } finally {
      setTokenUploadLoading(false)
    }
  }

  // Clear error when opening token modal
  const openTokenModal = (serverName) => {
    setTokenUploadError(null)
    setTokenModalServer(serverName)
  }

  // Handle disconnect confirmation
  const handleDisconnect = async () => {
    if (!disconnectServer) return
    setDisconnectError(null)
    try {
      await removeToken(disconnectServer)
      setDisconnectServer(null)
    } catch (err) {
      console.error('Token disconnect failed:', err)
      setDisconnectError(err?.message || 'Failed to disconnect. Please try again.')
    }
  }
  
  // Use compliance-filtered tools and prompts if feature is enabled, otherwise use marketplace filtered
  const complianceEnabled = features?.compliance_levels
  const tools = complianceEnabled ? getComplianceFilteredTools(complianceLevelFilter) : getFilteredTools()
  const prompts = complianceEnabled ? getComplianceFilteredPrompts(complianceLevelFilter) : getFilteredPrompts()
  
  // Extract unique compliance levels from all available tools and prompts
  const availableComplianceLevels = new Set()
  allTools.forEach(tool => {
    if (tool.compliance_level) availableComplianceLevels.add(tool.compliance_level)
  })
  allPrompts.forEach(prompt => {
    if (prompt.compliance_level) availableComplianceLevels.add(prompt.compliance_level)
  })

  const navigateToMarketplace = () => {
    clearToolsAndPrompts()
    navigate('/marketplace')
  }

  // Combine tools and prompts into a unified server list
  const allServers = {}
  
  // Add tools to the unified list
  tools.forEach(toolServer => {
    if (!allServers[toolServer.server]) {
      allServers[toolServer.server] = {
        server: toolServer.server,
        description: toolServer.description,
        short_description: toolServer.short_description,
        author: toolServer.author,
        help_email: toolServer.help_email,
        is_exclusive: toolServer.is_exclusive,
        compliance_level: toolServer.compliance_level,
        auth_type: toolServer.auth_type,
        tools: toolServer.tools || [],
        tools_detailed: toolServer.tools_detailed || [],
        tool_count: toolServer.tool_count || 0,
        prompts: [],
        prompt_count: 0
      }
    }
  })
  
  // Add prompts to the unified list
  prompts.forEach(promptServer => {
    if (!allServers[promptServer.server]) {
      allServers[promptServer.server] = {
        server: promptServer.server,
        description: promptServer.description,
        short_description: promptServer.short_description,
        author: promptServer.author,
        help_email: promptServer.help_email,
        is_exclusive: false,
        auth_type: promptServer.auth_type,
        tools: [],
        tools_detailed: [],
        tool_count: 0,
        prompts: promptServer.prompts || [],
        prompt_count: promptServer.prompt_count || 0
      }
    } else {
      allServers[promptServer.server].prompts = promptServer.prompts || []
      allServers[promptServer.server].prompt_count = promptServer.prompt_count || 0
      // Also update auth_type if not already set
      if (!allServers[promptServer.server].auth_type && promptServer.auth_type) {
        allServers[promptServer.server].auth_type = promptServer.auth_type
      }
    }
  })
  
  const serverList = Object.values(allServers)

  // Filter servers based on search term
  const filteredServers = serverList.filter(server => {
    if (!searchTerm) return true
    
    const searchLower = searchTerm.toLowerCase()
    
    // Search in server name and description
    if (server.server.toLowerCase().includes(searchLower) || 
        (server.description && server.description.toLowerCase().includes(searchLower))) {
      return true
    }
    
    // Search in tool names
    if (server.tools.some(tool => tool.toLowerCase().includes(searchLower))) {
      return true
    }
    
    // Search in prompt names and descriptions
    if (server.prompts.some(prompt => 
      prompt.name.toLowerCase().includes(searchLower) || 
      (prompt.description && prompt.description.toLowerCase().includes(searchLower))
    )) {
      return true
    }
    
    return false
  })

  /* -------------------------- Selection Utilities -------------------------- */
  const getServerByName = (serverName) => serverList.find(s => s.server === serverName)

  const getServerKeys = (server) => {
    return {
      toolKeys: server.tools.map(t => `${server.server}_${t}`),
      promptKeys: server.prompts.map(p => `${server.server}_${p.name}`)
    }
  }

  // Returns true if ALL tools AND ALL prompts are selected
  const isServerAllSelected = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return false
    const { toolKeys, promptKeys } = getServerKeys(server)
    const allToolsSelected = toolKeys.length === 0 || toolKeys.every(k => selectedTools.has(k))
    const allPromptsSelected = promptKeys.length === 0 || promptKeys.every(k => selectedPrompts.has(k))
    return allToolsSelected && allPromptsSelected
  }

  // Backward compat helper retained but now references "all selected" semantics
  const isServerSelected = (serverName) => isServerAllSelected(serverName)

  const toggleServerItems = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return

    const { toolKeys, promptKeys } = getServerKeys(server)
    const currentlySelected = isServerSelected(serverName)

    if (currentlySelected) {
      const toolsToRemove = toolKeys.filter(k => selectedTools.has(k))
      const promptsToRemove = promptKeys.filter(k => selectedPrompts.has(k))
      if (toolsToRemove.length) removeTools(toolsToRemove)
      if (promptsToRemove.length) removePrompts(promptsToRemove)
      return
    }

    const toolsToAdd = toolKeys.filter(k => !selectedTools.has(k))
    if (toolsToAdd.length) addTools(toolsToAdd)

    if (promptKeys.length > 0) {
      const promptsToAdd = promptKeys.filter(k => !selectedPrompts.has(k))
      if (promptsToAdd.length) addPrompts(promptsToAdd)
    }
  }


  const toggleToolExpansion = (toolKey) => {
    const newExpanded = new Set(expandedTools)
    if (newExpanded.has(toolKey)) {
      newExpanded.delete(toolKey)
    } else {
      newExpanded.add(toolKey)
    }
    setExpandedTools(newExpanded)
  }

  const toggleServerCollapse = (serverName) => {
    const newCollapsed = new Set(collapsedServers)
    if (newCollapsed.has(serverName)) {
      newCollapsed.delete(serverName)
    } else {
      newCollapsed.add(serverName)
    }
    setCollapsedServers(newCollapsed)
  }

  const toggleDescriptionExpansion = (serverName) => {
    const newExpanded = new Set(expandedDescriptions)
    if (newExpanded.has(serverName)) {
      newExpanded.delete(serverName)
    } else {
      newExpanded.add(serverName)
    }
    setExpandedDescriptions(newExpanded)
  }

  const togglePromptExpansion = (promptKey) => {
    const newExpanded = new Set(expandedPrompts)
    if (newExpanded.has(promptKey)) {
      newExpanded.delete(promptKey)
    } else {
      newExpanded.add(promptKey)
    }
    setExpandedPrompts(newExpanded)
  }

  /**
   * Truncates long prompt descriptions by showing start and end with ellipsis in the middle
   * @param {string} description - The full description text
   * @param {number} maxLength - Maximum length before truncation (default: 500)
   * @param {number} edgeLength - Number of characters to show at start and end (default: 200)
   * @returns {Object} Object with truncated text and isTruncated flag
   */
  const truncatePromptDescription = (description, maxLength = 500, edgeLength = 200) => {
    if (!description || description.length <= maxLength) {
      return { text: description, isTruncated: false }
    }
    
    const start = description.substring(0, edgeLength)
    const end = description.substring(description.length - edgeLength)
    return { 
      text: `${start}\n\n...\n\n${end}`,
      isTruncated: true
    }
  }

  /**
   * Renders the input schema parameters for a tool.
   * @param {Object} schema - The JSON schema object containing properties and required fields
   * @param {Object} schema.properties - Object mapping parameter names to their definitions
   * @param {Array<string>} [schema.required] - Array of required parameter names
   * @returns {JSX.Element} Formatted display of input parameters with types and descriptions
   */
  const renderInputSchema = (schema) => {
    if (!schema || !schema.properties) {
      return <p className="text-xs text-gray-400 italic">No input parameters</p>
    }
    
    const properties = schema.properties
    const required = schema.required || []
    
    return (
      <div className="space-y-1">
        {Object.entries(properties).map(([paramName, paramDef]) => (
          <div key={paramName} className="text-xs">
            <span className="font-mono text-blue-300">{paramName}</span>
            {required.includes(paramName) && (
              <span className="text-red-400 ml-1">*</span>
            )}
            <span className="text-gray-400 ml-2">({paramDef.type || DEFAULT_PARAM_TYPE})</span>
            {paramDef.description && (
              <p className="text-gray-400 ml-4 mt-0.5">{paramDef.description}</p>
            )}
          </div>
        ))}
      </div>
    )
  }

  // (Legacy isServerSelected removed; new implementation above.)

  if (!isOpen) return null

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={handleCloseAttempt}
    >
      <div 
        className="bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-lg font-semibold text-gray-100">Tools & Integrations</h2>
          <button
            onClick={handleCloseAttempt}
            className="p-1.5 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Controls Section */}
        <div className="px-4 py-3 border-b border-gray-700 flex-shrink-0 space-y-3">
          {/* Top Row: Add from Marketplace and Clear All */}
          <div className="flex gap-3">
            <button
              onClick={navigateToMarketplace}
              className="flex-1 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition-colors flex items-center justify-center gap-2"
            >
              <Plus className="w-4 h-4" />
              Add from Marketplace
            </button>
            <button
              onClick={handleClearAll}
              className="px-3 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm font-medium transition-colors flex items-center gap-2"
              title="Clear all tool and prompt selections"
            >
              <Trash2 className="w-3 h-3" />
              Clear All
            </button>
          </div>
          
          {/* Required Tool Usage Toggle */}
          <div className="flex items-center justify-between px-4 py-2 bg-gray-700 rounded-lg">
            <div>
              <h3 className="text-white text-sm font-medium">Required Tool Usage</h3>
              <p className="text-xs text-gray-400">Model must use selected tools to respond</p>
            </div>
            <button
              onClick={() => setToolChoiceRequired(!toolChoiceRequired)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 focus:ring-offset-gray-800 ${
                toolChoiceRequired ? 'bg-blue-600' : 'bg-gray-600'
              }`}
            >
              <span
                className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                  toolChoiceRequired ? 'translate-x-5' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        {/* Tools List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
          {serverList.length === 0 ? (
            <div className="text-gray-400 text-center py-12 px-6">
              <div className="text-lg mb-4">No servers selected</div>
              <p className="mb-6 text-gray-500">Add MCP servers from the marketplace to enable tools, integrations, and prompts</p>
              <button
                onClick={navigateToMarketplace}
                className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium"
              >
                Browse Marketplace
              </button>
            </div>
          ) : (
            <>
              {/* Section Header */}
              <div className="px-4 py-2 border-b border-gray-700">
                <h3 className="text-sm font-semibold text-white">
                  Your Installed Tools, Integrations, and Prompts ({serverList.reduce((total, server) => total + server.tool_count + server.prompt_count, 0)})
                </h3>
              </div>
              
              {/* Search Bar */}
              <div className="px-4 py-2">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-400 w-4 h-4" />
                  <input
                    type="text"
                    placeholder="Search installed tools..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
                  />
                </div>
              </div>
              
              {filteredServers.length === 0 ? (
                <div className="text-gray-400 text-center py-12 px-6">
                  <div className="text-lg mb-4">No results found</div>
                  <p className="text-gray-500">Try adjusting your search terms</p>
                </div>
              ) : (
                <div className="px-4 pb-4 space-y-3">
                  {filteredServers.map(server => {
                    const isCollapsed = collapsedServers.has(server.server)
                    const toolCount = server.tools.length
                    const promptCount = server.prompts.length
                    const totalItems = toolCount + promptCount
                    
                    return (
                      <div key={server.server} className="bg-gray-700 rounded-lg overflow-hidden">
                        {/* Main Server Row */}
                        <div className="p-2 flex items-start gap-2">
                          {/* Collapse/Expand Button */}
                          <button
                            onClick={() => toggleServerCollapse(server.server)}
                            className="flex-shrink-0 p-1 hover:bg-gray-600 rounded transition-colors"
                            title={isCollapsed ? 'Expand server' : 'Collapse server'}
                          >
                            {isCollapsed ? (
                              <ChevronRight className="w-4 h-4 text-gray-300" />
                            ) : (
                              <ChevronDown className="w-4 h-4 text-gray-300" />
                            )}
                          </button>
                          
                          {/* Server Icon */}
                          <div className="bg-gray-600 rounded p-1.5 flex-shrink-0">
                            <Server className="w-3 h-3 text-gray-300" />
                          </div>
                          
                          {/* Server Content */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <h3 className="text-white font-medium text-base capitalize truncate">
                                {server.server}
                              </h3>
                              <span className="text-xs text-gray-400 flex-shrink-0">
                                ({totalItems} {totalItems === 1 ? 'item' : 'items'})
                              </span>
                              {server.is_exclusive && (
                                <span className="px-1.5 py-0.5 bg-orange-600 text-xs rounded text-white flex-shrink-0">
                                  Exclusive
                                </span>
                              )}
                              {complianceEnabled && server.compliance_level && (
                                <span className="px-1.5 py-0.5 bg-blue-600 text-xs rounded text-white flex items-center gap-1 flex-shrink-0">
                                  <Shield className="w-3 h-3" />
                                  {server.compliance_level}
                                </span>
                              )}
                              {/* Auth Status Indicator - for API key/JWT/bearer servers (not OAuth) */}
                              {(server.auth_type === 'jwt' || server.auth_type === 'bearer' || server.auth_type === 'api_key') && (() => {
                                const serverAuth = getServerAuth(server.server)
                                const isAuthenticated = serverAuth?.authenticated && !serverAuth?.is_expired
                                return (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      if (isAuthenticated) {
                                        setDisconnectServer(server.server)
                                      } else {
                                        openTokenModal(server.server)
                                      }
                                    }}
                                    className={`flex-shrink-0 p-1 rounded ${
                                      isAuthenticated
                                        ? 'bg-green-600/20 hover:bg-green-600/30 text-green-400'
                                        : 'bg-yellow-600/20 hover:bg-yellow-600/30 text-yellow-400'
                                    }`}
                                    title={isAuthenticated ? 'Authenticated. Click to disconnect.' : 'Click to add token.'}
                                  >
                                    {isAuthenticated ? <ShieldCheck className="w-4 h-4" /> : <Key className="w-4 h-4" />}
                                  </button>
                                )
                              })()}
                            </div>
                            
                            {/* Short description - always shown (compact) */}
                            {server.short_description && (
                              <p className="text-xs text-gray-400 mb-1 line-clamp-1">
                                {server.short_description}
                              </p>
                            )}
                            
                            {/* If no short_description but has description, show description (compact) */}
                            {!server.short_description && server.description && (
                              <p className="text-xs text-gray-400 mb-1 line-clamp-1">
                                {server.description}
                              </p>
                            )}
                            
                            {/* Expandable full description - only if different from short_description */}
                            {server.description && server.description !== server.short_description && (
                              <div className="mb-1">
                                {expandedDescriptions.has(server.server) ? (
                                  <>
                                    <p className="text-xs text-gray-400 mb-1">
                                      {server.description}
                                    </p>
                                    <button
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        toggleDescriptionExpansion(server.server)
                                      }}
                                      className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                                    >
                                      Show less
                                    </button>
                                  </>
                                ) : (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      toggleDescriptionExpansion(server.server)
                                    }}
                                    className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
                                  >
                                    Show more details...
                                  </button>
                                )}
                              </div>
                            )}
                            
                            {/* Author and help email info */}
                            <div className="flex items-center gap-3 mb-2 text-xs text-gray-500">
                              {server.author && (
                                <div className="flex items-center gap-1">
                                  <User className="w-3 h-3" />
                                  <span>{server.author}</span>
                                </div>
                              )}
                              {server.help_email && (
                                <div className="flex items-center gap-1">
                                  <Mail className="w-3 h-3" />
                                  <a 
                                    href={`mailto:${server.help_email}`}
                                    className="hover:text-blue-400 transition-colors"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    {server.help_email}
                                  </a>
                                </div>
                              )}
                            </div>
                            
                            {/* Tools and Prompts - only show when not collapsed */}
                            {!isCollapsed && (
                              <>
                                {/* Tools Display */}
                                {server.tools.length > 0 && (
                                  <div className="mb-4">
                                    <div className="flex items-center gap-1 mb-1">
                                      <Wrench className="w-3 h-3 text-white" />
                                      <span className="text-sm font-bold text-white">Tools</span>
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                {server.tools.map(tool => {
                                    const toolKey = `${server.server}_${tool}`
                                    const isSelected = selectedTools.has(toolKey)
                                    const isToolExpanded = expandedTools.has(toolKey)
                                    // Find detailed tool info
                                    const toolDetail = server.tools_detailed?.find(t => t.name === tool)
                                    
                                    return (
                                      <div key={tool} className="flex flex-col gap-1 w-full">
                                        <div className="flex items-center gap-1">
                                          <button
                                            onClick={() => {
            // Toggle ONLY this specific tool
            toggleTool(toolKey)
                                            }}
                                            className={`px-2 py-0.5 text-xs rounded text-white transition-colors hover:opacity-80 ${
                                              isSelected ? 'bg-green-600' : 'bg-gray-600 hover:bg-green-600'
                                            }`}
                                            title={`Click to ${isSelected ? 'disable' : 'enable'} ${tool}`}
                                          >
                                            {tool}
                                          </button>
                                          {toolDetail && (
                                            <button
                                              onClick={() => toggleToolExpansion(toolKey)}
                                              className="p-0.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-300 transition-colors"
                                              title="Show tool details"
                                            >
                                              <Info className="w-3 h-3" />
                                            </button>
                                          )}
                                        </div>
                                        {isToolExpanded && toolDetail && (
                                          <div className="bg-gray-800 rounded p-2 text-xs space-y-2 border border-gray-600">
                                            {toolDetail.description && (
                                              <div>
                                                <p className="font-semibold text-gray-300 mb-1">Description:</p>
                                                <p className="text-gray-400">{toolDetail.description}</p>
                                              </div>
                                            )}
                                            <div>
                                              <p className="font-semibold text-gray-300 mb-1">Input Arguments:</p>
                                              {renderInputSchema(toolDetail.inputSchema)}
                                            </div>
                                          </div>
                                        )}
                                      </div>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                            
                            {/* Divider between Tools and Prompts */}
                            {server.tools.length > 0 && server.prompts.length > 0 && (
                              <div className="h-px bg-gray-500 opacity-60 my-3"></div>
                            )}
                            
                            {/* Prompts Display */}
                            {server.prompts.length > 0 && (
                              <div className="mb-2">
                                <div className="flex items-center gap-1 mb-1">
                                  <Sparkles className="w-3 h-3 text-white" />
                                  <span className="text-sm font-bold text-white">Prompts</span>
                                </div>
                                <div className="flex flex-wrap gap-1">
                                  {server.prompts.map(prompt => {
                                    const promptKey = `${server.server}_${prompt.name}`
                                    const isSelected = selectedPrompts.has(promptKey)
                                    const isPromptExpanded = expandedPrompts.has(promptKey)

                                    return (
                                      <div key={prompt.name} className="flex flex-col gap-1 w-full">
                                        <div className="flex items-center gap-1">
                                          <button
                                            onClick={() => togglePrompt(promptKey)}
                                            className={`px-2 py-0.5 text-xs rounded text-white transition-colors hover:opacity-80 ${
                                              isSelected ? 'bg-green-600' : 'bg-gray-600 hover:bg-green-600'
                                            }`}
                                            title={`Click to ${isSelected ? 'disable' : 'enable'} ${prompt.name}`}
                                          >
                                            {prompt.name}
                                          </button>
                                          {prompt.description && (
                                            <button
                                              onClick={() => togglePromptExpansion(promptKey)}
                                              className="p-0.5 rounded bg-gray-600 hover:bg-gray-500 text-gray-300 transition-colors"
                                              title="Show prompt description"
                                            >
                                              <Info className="w-3 h-3" />
                                            </button>
                                          )}
                                        </div>
                                        {isPromptExpanded && prompt.description && (() => {
                                          const { text: truncatedDescription, isTruncated } = truncatePromptDescription(prompt.description)
                                          return (
                                            <div className="bg-gray-800 rounded p-2 text-xs space-y-2 border border-gray-600">
                                              <div>
                                                <p className="font-semibold text-gray-300 mb-1">Description:</p>
                                                <p className="text-gray-400 whitespace-pre-wrap">{truncatedDescription}</p>
                                                {isTruncated && (
                                                  <p className="text-xs text-yellow-400 mt-2 italic">
                                                    {TRUNCATION_MESSAGE}
                                                  </p>
                                                )}
                                              </div>
                                            </div>
                                          )
                                        })()}
                                      </div>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                              </>
                            )}
                          </div>
                          
                          {/* Action Buttons */}
                          <div className="flex flex-col items-end gap-1 flex-shrink-0">
                            {/* Enable All Button */}
                            <button
                              onClick={() => toggleServerItems(server.server)}
                              className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                                isServerAllSelected(server.server)
                                  ? 'bg-green-600 hover:bg-green-700 text-white'
                                  : 'bg-gray-600 hover:bg-gray-500 text-gray-200'
                              }`}
                              title="Toggle all tools and prompts for this server"
                            >
                              {isServerAllSelected(server.server) ? 'All On' : 'Enable All'}
                            </button>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
              
            </>
          )}
        </div>
        
        {/* Footer with Save/Cancel buttons */}
        <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-gray-700 flex-shrink-0">
          <button
            onClick={handleCancel}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors font-medium ${
              hasChanges
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'bg-gray-600 text-gray-400 cursor-not-allowed'
            }`}
          >
            <Save className="w-4 h-4" />
            Save Changes
          </button>
        </div>
      </div>

      {/* Unsaved Changes Confirmation Dialog */}
      <UnsavedChangesDialog
        isOpen={showUnsavedDialog}
        onSave={handleSaveAndClose}
        onDiscard={handleDiscardAndClose}
        onCancel={handleCancelDialog}
      />

      {/* Token Input Modal for JWT/bearer auth */}
      <TokenInputModal
        isOpen={tokenModalServer !== null}
        serverName={tokenModalServer}
        onClose={() => {
          setTokenModalServer(null)
          setTokenUploadError(null)
        }}
        onUpload={handleTokenUpload}
        isLoading={tokenUploadLoading}
        error={tokenUploadError}
      />

      {/* Disconnect Confirmation Modal */}
      {disconnectServer && (
        <div
          className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-[100]"
          onClick={() => { setDisconnectServer(null); setDisconnectError(null) }}
        >
          <div
            className="bg-gray-800 rounded-lg shadow-xl max-w-sm w-full mx-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-gray-100 mb-4">
              Disconnect from {disconnectServer}?
            </h3>
            <p className="text-gray-400 text-sm mb-4">
              This will remove your saved token for this server. You'll need to re-enter it to use this server again.
            </p>
            {disconnectError && (
              <div className="p-3 mb-4 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
                {disconnectError}
              </div>
            )}
            <div className="flex justify-end gap-3">
              <button
                onClick={() => { setDisconnectServer(null); setDisconnectError(null) }}
                className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDisconnect}
                className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white transition-colors"
              >
                Disconnect
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ToolsPanel
