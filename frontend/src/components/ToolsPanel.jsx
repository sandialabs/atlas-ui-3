import { X, Trash2, Search, Plus, Wrench, ChevronDown, ChevronUp } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

const ToolsPanel = ({ isOpen, onClose }) => {
  const [searchTerm, setSearchTerm] = useState('')
  const [expandedServers, setExpandedServers] = useState(new Set())
  const navigate = useNavigate()
  const { 
    selectedTools, 
    toggleTool, 
    selectedPrompts,
    togglePrompt,
  addTools,
  removeTools,
  setSinglePrompt,
  removePrompts,
    toolChoiceRequired, 
    setToolChoiceRequired,
    clearToolsAndPrompts
  } = useChat()
  const { getFilteredTools, getFilteredPrompts } = useMarketplace()
  
  // Use filtered tools and prompts instead of all tools
  const tools = getFilteredTools()
  const prompts = getFilteredPrompts()
  
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
        is_exclusive: toolServer.is_exclusive,
        tools: toolServer.tools || [],
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
        is_exclusive: false,
        tools: [],
        tool_count: 0,
        prompts: promptServer.prompts || [],
        prompt_count: promptServer.prompt_count || 0
      }
    } else {
      allServers[promptServer.server].prompts = promptServer.prompts || []
      allServers[promptServer.server].prompt_count = promptServer.prompt_count || 0
    }
  })
  
  const serverList = Object.values(allServers)

  // Ensure only one prompt remains selected globally if storage had more
  useEffect(() => {
    if (selectedPrompts && selectedPrompts.size > 1) {
      const first = Array.from(selectedPrompts)[0]
      setSinglePrompt(first)
    }
  }, [selectedPrompts, setSinglePrompt])

  // Derive currently selected prompt (if any)
  const selectedPromptKey = selectedPrompts && selectedPrompts.size > 0
    ? Array.from(selectedPrompts)[0]
    : null

  const selectedPromptInfo = (() => {
    if (!selectedPromptKey) return null
    const idx = selectedPromptKey.indexOf('_')
    if (idx === -1) return { key: selectedPromptKey, server: 'Unknown', name: selectedPromptKey }
    const server = selectedPromptKey.slice(0, idx)
    const name = selectedPromptKey.slice(idx + 1)
    // Try to find description from our server list
    const srv = serverList.find(s => s.server === server)
    const desc = srv?.prompts?.find(p => p.name === name)?.description || ''
    return { key: selectedPromptKey, server, name, description: desc }
  })()

  // Filter servers based on search term
  const filteredServers = serverList.filter(server => {
    if (!searchTerm) return true
    
    const searchLower = searchTerm.toLowerCase()
    
    // Search in server name and description
    if (server.server.toLowerCase().includes(searchLower) || 
        server.description.toLowerCase().includes(searchLower)) {
      return true
    }
    
    // Search in tool names
    if (server.tools.some(tool => tool.toLowerCase().includes(searchLower))) {
      return true
    }
    
    // Search in prompt names and descriptions
    if (server.prompts.some(prompt => 
      prompt.name.toLowerCase().includes(searchLower) || 
      prompt.description.toLowerCase().includes(searchLower)
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

  // Returns true if ANY tool or prompt from this server is selected
  const isServerEnabledAny = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return false
    const { toolKeys, promptKeys } = getServerKeys(server)
    const anyTool = toolKeys.some(k => selectedTools.has(k))
    const anyPrompt = promptKeys.some(k => selectedPrompts.has(k))
    return anyTool || anyPrompt
  }

  // Returns true if ALL tools are selected AND (if prompts exist) one prompt is selected
  const isServerAllSelected = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return false
    const { toolKeys, promptKeys } = getServerKeys(server)
    const allToolsSelected = toolKeys.length === 0 || toolKeys.every(k => selectedTools.has(k))
    const promptSatisfied = promptKeys.length === 0 || promptKeys.some(k => selectedPrompts.has(k))
    return allToolsSelected && promptSatisfied
  }

  const ensureSinglePrompt = (promptKey) => {
    // Deselect all other prompts
    Array.from(selectedPrompts).forEach(existing => {
      if (existing !== promptKey) togglePrompt(existing)
    })
    if (!selectedPrompts.has(promptKey)) togglePrompt(promptKey)
  }

  const handlePromptCheckbox = (promptKey) => {
    if (selectedPrompts.has(promptKey)) {
      // Deselect current prompt
      togglePrompt(promptKey)
    } else {
      ensureSinglePrompt(promptKey)
    }
  }

  // Backward compat helper retained but now references "all selected" semantics
  const isServerSelected = (serverName) => isServerAllSelected(serverName)

  const toggleServerItems = (serverName) => {
    console.debug('[TOOLS_PANEL] toggleServerItems invoked', { serverName })
    const server = getServerByName(serverName)
    if (!server) {
      console.debug('[TOOLS_PANEL] server not found, aborting', { serverName })
      return
    }
    const { toolKeys, promptKeys } = getServerKeys(server)
    const currentlySelected = isServerSelected(serverName)
    console.debug('[TOOLS_PANEL] current state snapshot BEFORE', {
      serverName,
      toolKeys,
      promptKeys,
      selectedTools: Array.from(selectedTools),
      selectedPrompts: Array.from(selectedPrompts),
      currentlySelected
    })

    if (currentlySelected) {
      console.debug('[TOOLS_PANEL] Deselecting all items for server', { serverName })
      const toolsToRemove = toolKeys.filter(k => selectedTools.has(k))
      const promptsToRemove = promptKeys.filter(k => selectedPrompts.has(k))
      if (toolsToRemove.length) {
        console.debug('[TOOLS_PANEL] batch remove tools', toolsToRemove)
        removeTools(toolsToRemove)
      }
      if (promptsToRemove.length) {
        console.debug('[TOOLS_PANEL] batch remove prompts', promptsToRemove)
        removePrompts(promptsToRemove)
      }
      // Log after microtask so state updates propagate
      setTimeout(() => {
        console.debug('[TOOLS_PANEL] state AFTER deselect', {
          serverName,
            selectedTools: Array.from(selectedTools),
            selectedPrompts: Array.from(selectedPrompts),
            serverSelectedNow: isServerSelected(serverName)
        })
      }, 0)
      return
    }

    console.debug('[TOOLS_PANEL] Selecting all tools for server', { serverName })
    const toolsToAdd = toolKeys.filter(k => !selectedTools.has(k))
    if (toolsToAdd.length) {
      console.debug('[TOOLS_PANEL] batch add tools', toolsToAdd)
      addTools(toolsToAdd)
    }

    // If server has prompts choose first (or existing) and enforce single-prompt global rule
    if (promptKeys.length > 0) {
      const alreadyOne = promptKeys.find(k => selectedPrompts.has(k))
      console.debug('[TOOLS_PANEL] handling prompts for server', { serverName, promptKeys, alreadyOne })
      setSinglePrompt(alreadyOne || promptKeys[0])
    } else {
      console.debug('[TOOLS_PANEL] no prompts for this server', { serverName })
    }

    setTimeout(() => {
      console.debug('[TOOLS_PANEL] state AFTER select', {
        serverName,
        selectedTools: Array.from(selectedTools),
        selectedPrompts: Array.from(selectedPrompts),
        serverSelectedNow: isServerSelected(serverName)
      })
    }, 0)
  }

  // Enable with a minimal default: if no items selected, select first tool, else first prompt
  const enableServerMinimal = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return
    const { toolKeys, promptKeys } = getServerKeys(server)
    // Prefer first tool if available
    if (toolKeys.length > 0) {
      const first = toolKeys[0]
      if (!selectedTools.has(first)) toggleTool(first)
      return
    }
    // Else, pick first prompt (enforcing single prompt)
    if (promptKeys.length > 0) {
      setSinglePrompt(promptKeys[0])
    }
  }

  // Disable everything for this server
  const disableServerAll = (serverName) => {
    const server = getServerByName(serverName)
    if (!server) return
    const { toolKeys, promptKeys } = getServerKeys(server)
    const toolsToRemove = toolKeys.filter(k => selectedTools.has(k))
    const promptsToRemove = promptKeys.filter(k => selectedPrompts.has(k))
    if (toolsToRemove.length) removeTools(toolsToRemove)
    if (promptsToRemove.length) removePrompts(promptsToRemove)
  }


  const toggleServerExpansion = (serverName) => {
    const newExpanded = new Set(expandedServers)
    if (newExpanded.has(serverName)) {
      newExpanded.delete(serverName)
    } else {
      newExpanded.add(serverName)
    }
    setExpandedServers(newExpanded)
  }

  // (Legacy isServerSelected removed; new implementation above.)

  if (!isOpen) return null

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div 
        className="bg-gray-800 rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-lg font-semibold text-gray-100">Tools & Integrations</h2>
          <button
            onClick={onClose}
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
              onClick={clearToolsAndPrompts}
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

          {/* Selected Prompt Summary */}
          <div className="flex items-center justify-between px-4 py-2 bg-gray-700 rounded-lg">
            <div className="flex-1 min-w-0">
              <h3 className="text-white text-sm font-medium">Selected Prompt</h3>
              {selectedPromptInfo ? (
                <p className="text-xs text-gray-300 truncate">
                  <span className="font-semibold text-purple-300">{selectedPromptInfo.name}</span>
                  <span className="text-gray-400"> from {selectedPromptInfo.server}</span>
                </p>
              ) : (
                <p className="text-xs text-gray-400">None selected</p>
              )}
            </div>
            {selectedPromptInfo && (
              <button
                onClick={() => setSinglePrompt(null)}
                className="px-2 py-1 rounded text-xs font-medium bg-gray-600 hover:bg-gray-500 text-gray-100 transition-colors flex-shrink-0 ml-2"
                title="Clear selected prompt"
              >
                Clear
              </button>
            )}
          </div>
        </div>

        {/* Tools List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0">
          {serverList.length === 0 ? (
            <div className="text-gray-400 text-center py-12 px-6">
              <div className="text-lg mb-4">No servers selected</div>
              <p className="mb-6 text-gray-500">Add MCP servers from the marketplace to enable tools and integrations</p>
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
                  Your Installed Tools ({serverList.reduce((total, server) => total + server.tool_count + server.prompt_count, 0)})
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
                    const isExpanded = expandedServers.has(server.server)
                    const hasIndividualItems = server.tools.length > 0 || server.prompts.length > 0
                    
                    return (
                      <div key={server.server} className="bg-gray-700 rounded-lg overflow-hidden">
                        {/* Main Server Row */}
                        <div className="p-2 flex items-start gap-2">
                          {/* Server Icon */}
                          <div className="bg-gray-600 rounded p-1.5 flex-shrink-0">
                            <Wrench className="w-3 h-3 text-gray-300" />
                          </div>
                          
                          {/* Server Content */}
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <h3 className="text-white font-medium text-base capitalize truncate">
                                {server.server}
                              </h3>
                              {server.is_exclusive && (
                                <span className="px-1.5 py-0.5 bg-orange-600 text-xs rounded text-white flex-shrink-0">
                                  Exclusive
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-gray-400 mb-2 line-clamp-1">{server.description}</p>
                            
                            {/* Tools Display */}
                            {server.tools.length > 0 && (
                              <div className="mb-1">
                                <div className="flex flex-wrap gap-1">
          {server.tools.map(tool => {
                                    const toolKey = `${server.server}_${tool}`
                                    const isSelected = selectedTools.has(toolKey)
                                    return (
                                      <button
                                        key={tool}
                                        onClick={() => {
            // Toggle ONLY this specific tool
            toggleTool(toolKey)
                                        }}
                                        className={`px-2 py-0.5 text-xs rounded text-white transition-colors hover:opacity-80 ${
                                          isSelected ? 'bg-blue-600' : 'bg-gray-600 hover:bg-blue-600'
                                        }`}
                                        title={`Click to ${isSelected ? 'disable' : 'enable'} ${tool}`}
                                      >
                                        {tool}
                                      </button>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                            
                            {/* Prompts Display */}
                            {server.prompts.length > 0 && (
                              <div className="mb-1">
                                <div className="flex flex-wrap gap-1">
                                  {server.prompts.map(prompt => {
                                    const promptKey = `${server.server}_${prompt.name}`
                                    const isSelected = selectedPrompts.has(promptKey)
                                    return (
                                      <button
                                        key={prompt.name}
                                        onClick={() => {
                                          if (!isSelected) {
                                            // Set this prompt as the single selected prompt
                                            setSinglePrompt(promptKey)
                                          } else {
                                            // Deselect this prompt
                                            togglePrompt(promptKey)
                                          }
                                        }}
                                        className={`px-2 py-0.5 text-xs rounded text-white transition-colors hover:opacity-80 ${
                                          isSelected ? 'bg-purple-600' : 'bg-gray-600 hover:bg-purple-600'
                                        }`}
                                        title={`${prompt.description}\n\nClick to ${isSelected ? 'disable' : 'enable'} ${prompt.name}`}
                                      >
                                        {prompt.name}
                                      </button>
                                    )
                                  })}
                                </div>
                              </div>
                            )}
                          </div>
                          
                          {/* Action Buttons */}
                          <div className="flex flex-col items-end gap-1 flex-shrink-0">
                            {/* Enable buttons in a horizontal row */}
                            <div className="flex items-center gap-1">
                              {/* Enable (any) Button */}
                              <button
                                onClick={() => {
                                  if (isServerEnabledAny(server.server)) {
                                    // Disable everything for this server
                                    disableServerAll(server.server)
                                  } else {
                                    // Enable minimally (first tool or prompt)
                                    enableServerMinimal(server.server)
                                  }
                                }}
                                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                                  isServerEnabledAny(server.server)
                                    ? 'bg-blue-600 hover:bg-blue-700 text-white'
                                    : 'bg-gray-600 hover:bg-gray-500 text-gray-200'
                                }`}
                                title="Enable this server (at least one item)"
                              >
                                {isServerEnabledAny(server.server) ? 'Enabled' : 'Enable'}
                              </button>

                              {/* Enable All Button */}
                              <button
                                onClick={() => toggleServerItems(server.server)}
                                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                                  isServerAllSelected(server.server)
                                    ? 'bg-green-600 hover:bg-green-700 text-white'
                                    : 'bg-gray-600 hover:bg-gray-500 text-gray-200'
                                }`}
                                title="Enable all tools (and choose a prompt if available)"
                              >
                                {isServerAllSelected(server.server) ? 'All On' : 'Enable All'}
                              </button>
                            </div>

                            {/* Expand Button */}
                            {hasIndividualItems && (
                              <button
                                onClick={() => toggleServerExpansion(server.server)}
                                className="p-1 rounded bg-gray-600 hover:bg-gray-500 text-gray-300 transition-colors"
                                title="Manage individual tools"
                              >
                                {isExpanded ? (
                                  <ChevronUp className="w-3 h-3" />
                                ) : (
                                  <ChevronDown className="w-3 h-3" />
                                )}
                              </button>
                            )}
                          </div>
                        </div>
                        
                        {/* Expanded Individual Tools Section */}
                        {isExpanded && hasIndividualItems && (
                          <div className="px-4 pb-4 border-t border-gray-600 bg-gray-800">
                            <div className="pt-4 space-y-3">
                              <p className="text-sm text-gray-400 mb-3">
                                Select individual tools and prompts:
                              </p>
                              
                              {/* Tools */}
                              {server.tools.length > 0 && (
                                <div>
                                  <h4 className="text-sm font-medium text-gray-300 mb-2">Tools</h4>
                                  <div className="space-y-2">
                                    {server.tools.map(tool => {
                                      const toolKey = `${server.server}_${tool}`
                                      const isSelected = selectedTools.has(toolKey)
                                      
                                      return (
                                        <label
                                          key={toolKey}
                                          className="flex items-center gap-3 p-2 rounded bg-gray-600 hover:bg-gray-500 cursor-pointer transition-colors"
                                        >
                                          <input
                                            type="checkbox"
                                            checked={isSelected}
                                            onChange={() => toggleTool(toolKey)}
                                            className="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded focus:ring-blue-500 focus:ring-2"
                                          />
                                          <span className="text-sm text-gray-200">{tool}</span>
                                        </label>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}
                              
                              {/* Prompts */}
                              {server.prompts.length > 0 && (
                                <div>
                                  <h4 className="text-sm font-medium text-purple-300 mb-2">Prompts</h4>
                                  <div className="space-y-2">
                                    {server.prompts.map(prompt => {
                                      const promptKey = `${server.server}_${prompt.name}`
                                      const isSelected = selectedPrompts.has(promptKey)
                                      
                                      return (
                                        <label
                                          key={promptKey}
                                          className="flex items-center gap-3 p-2 rounded bg-purple-900 hover:bg-purple-800 cursor-pointer transition-colors"
                                          title={prompt.description}
                                        >
                                          <input
                                            type="checkbox"
                                            checked={isSelected}
                                            onChange={() => handlePromptCheckbox(promptKey)}
                                            className="w-4 h-4 text-purple-600 bg-gray-700 border-gray-600 rounded focus:ring-purple-500 focus:ring-2"
                                          />
                                          <div className="flex-1 min-w-0">
                                            <span className="text-sm text-gray-200 block truncate">{prompt.name}</span>
                                            {prompt.description && (
                                              <span className="text-xs text-gray-400 block truncate mt-1">
                                                {prompt.description}
                                              </span>
                                            )}
                                          </div>
                                        </label>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default ToolsPanel