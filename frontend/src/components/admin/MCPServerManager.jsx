import React, { useState, useEffect, useCallback } from 'react'
import { Plus, Minus, RefreshCw, Server, CheckCircle, XCircle, AlertCircle, Search } from 'lucide-react'

const MCPServerManager = ({ addNotification }) => {
  const [availableServers, setAvailableServers] = useState({})
  const [activeServers, setActiveServers] = useState({})
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState({})
  const [searchQuery, setSearchQuery] = useState('')

  const loadServers = useCallback(async () => {
    try {
      setLoading(true)
      
      // Load available servers
      const availableResponse = await fetch('/admin/mcp/available-servers')
      if (!availableResponse.ok) {
        throw new Error(`Failed to load available servers: ${availableResponse.status}`)
      }
      const availableData = await availableResponse.json()
      
      // Load active servers
      const activeResponse = await fetch('/admin/mcp/active-servers')
      if (!activeResponse.ok) {
        throw new Error(`Failed to load active servers: ${activeResponse.status}`)
      }
      const activeData = await activeResponse.json()
      
      setAvailableServers(availableData.available_servers || {})
      setActiveServers(activeData.active_servers || {})
      
    } catch (err) {
      console.error('Error loading MCP servers:', err)
      addNotification('Error loading MCP servers: ' + err.message, 'error')
    } finally {
      setLoading(false)
    }
  }, [addNotification])

  useEffect(() => {
    loadServers()
  }, [loadServers])

  const addServer = async (serverName) => {
    try {
      setActionLoading(prev => ({ ...prev, [serverName]: 'adding' }))
      
      const response = await fetch('/admin/mcp/add-server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: serverName })
      })
      
      const result = await response.json()
      
      if (!response.ok) {
        throw new Error(result.detail || `HTTP ${response.status}`)
      }
      
      if (result.already_active) {
        addNotification(result.message, 'info')
      } else {
        addNotification(result.message, 'success')
        // Reload servers to get updated state
        await loadServers()
      }
      
    } catch (err) {
      addNotification('Error adding server: ' + err.message, 'error')
    } finally {
      setActionLoading(prev => ({ ...prev, [serverName]: null }))
    }
  }

  const removeServer = async (serverName) => {
    try {
      setActionLoading(prev => ({ ...prev, [serverName]: 'removing' }))
      
      const response = await fetch('/admin/mcp/remove-server', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server_name: serverName })
      })
      
      const result = await response.json()
      
      if (!response.ok) {
        throw new Error(result.detail || `HTTP ${response.status}`)
      }
      
      if (result.not_active) {
        addNotification(result.message, 'info')
      } else {
        addNotification(result.message, 'success')
        // Reload servers to get updated state
        await loadServers()
      }
      
    } catch (err) {
      addNotification('Error removing server: ' + err.message, 'error')
    } finally {
      setActionLoading(prev => ({ ...prev, [serverName]: null }))
    }
  }

  const getComplianceLevelColor = (level) => {
    switch (level?.toLowerCase()) {
      case 'public': return 'text-green-400 bg-green-900/20'
      case 'soc2': return 'text-yellow-400 bg-yellow-900/20'
      case 'restricted': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  const getServerStatus = (serverName) => {
    return serverName in activeServers ? 'active' : 'available'
  }

  const getStatusIcon = (status) => {
    switch (status) {
      case 'active': return <CheckCircle className="w-4 h-4 text-green-400" />
      case 'available': return <XCircle className="w-4 h-4 text-gray-400" />
      default: return <AlertCircle className="w-4 h-4 text-yellow-400" />
    }
  }

  if (loading) {
    return (
      <div className="bg-gray-800 rounded-lg p-6">
        <div className="flex items-center gap-3 mb-4">
          <Server className="w-6 h-6 text-blue-400" />
          <h2 className="text-lg font-semibold">MCP Server Manager</h2>
        </div>
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
          <span className="ml-2 text-gray-400">Loading servers...</span>
        </div>
      </div>
    )
  }

  const allServerNames = new Set([
    ...Object.keys(availableServers),
    ...Object.keys(activeServers)
  ])

  // Filter servers based on search query
  const filteredServerNames = Array.from(allServerNames).filter(serverName => {
    if (!searchQuery.trim()) return true
    const query = searchQuery.toLowerCase()
    const serverInfo = availableServers[serverName]
    return (
      serverName.toLowerCase().includes(query) ||
      serverInfo?.description?.toLowerCase().includes(query) ||
      serverInfo?.short_description?.toLowerCase().includes(query) ||
      serverInfo?.author?.toLowerCase().includes(query)
    )
  })

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Server className="w-6 h-6 text-blue-400" />
          <h2 className="text-lg font-semibold">MCP Server Manager</h2>
        </div>
        <button
          onClick={loadServers}
          disabled={loading}
          className="flex items-center gap-2 px-3 py-1 bg-gray-700 hover:bg-gray-600 rounded text-sm transition-colors"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
      
      <p className="text-gray-400 mb-4">
        Manage MCP servers by adding them from available configurations (loaded from the <code className="text-gray-300">config/mcp-example-configs/</code> folder) or removing them from active use.
      </p>

      {/* Search input */}
      <div className="relative mb-4">
        <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
        <input
          type="text"
          placeholder="Search servers..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
        />
      </div>

      <div className="space-y-3 max-h-96 overflow-y-auto">
        {Array.from(filteredServerNames).sort().map(serverName => {
          const serverInfo = availableServers[serverName]
          const status = getServerStatus(serverName)
          const isActive = status === 'active'
          const currentAction = actionLoading[serverName]
          
          return (
            <div
              key={serverName}
              className={`border rounded-lg p-4 transition-colors ${
                isActive 
                  ? 'border-green-600 bg-green-900/10' 
                  : 'border-gray-600 bg-gray-700/50'
              }`}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    {getStatusIcon(status)}
                    <h3 className="font-medium text-white">{serverName}</h3>
                    {serverInfo?.compliance_level && (
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${getComplianceLevelColor(serverInfo.compliance_level)}`}>
                        {serverInfo.compliance_level}
                      </span>
                    )}
                  </div>
                  
                  {serverInfo?.short_description && (
                    <p className="text-sm text-gray-300 mb-1">
                      {serverInfo.short_description}
                    </p>
                  )}
                  
                  {serverInfo?.description && serverInfo.description !== serverInfo.short_description && (
                    <p className="text-xs text-gray-400 mb-2">
                      {serverInfo.description}
                    </p>
                  )}
                  
                  <div className="flex items-center gap-4 text-xs text-gray-500">
                    {serverInfo?.author && (
                      <span>Author: {serverInfo.author}</span>
                    )}
                    {serverInfo?.source_file && (
                      <span>Source: {serverInfo.source_file}</span>
                    )}
                  </div>
                </div>
                
                <div className="flex items-center gap-2 ml-4">
                  {isActive ? (
                    <button
                      onClick={() => removeServer(serverName)}
                      disabled={currentAction === 'removing'}
                      className="flex items-center gap-1 px-3 py-1 bg-red-600 hover:bg-red-700 disabled:bg-red-900 disabled:opacity-60 text-white rounded text-sm transition-colors"
                    >
                      {currentAction === 'removing' ? (
                        <RefreshCw className="w-4 h-4 animate-spin" />
                      ) : (
                        <Minus className="w-4 h-4" />
                      )}
                      {currentAction === 'removing' ? 'Removing...' : 'Remove'}
                    </button>
                  ) : (
                    <button
                      onClick={() => addServer(serverName)}
                      disabled={currentAction === 'adding'}
                      className="flex items-center gap-1 px-3 py-1 bg-green-600 hover:bg-green-700 disabled:bg-green-900 disabled:opacity-60 text-white rounded text-sm transition-colors"
                    >
                      {currentAction === 'adding' ? (
                        <RefreshCw className="w-4 h-4 animate-spin" />
                      ) : (
                        <Plus className="w-4 h-4" />
                      )}
                      {currentAction === 'adding' ? 'Adding...' : 'Add'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )
        })}
        
        {filteredServerNames.length === 0 && (
          <div className="text-center py-8 text-gray-500">
            <Server className="w-12 h-12 mx-auto mb-2 opacity-50" />
            {searchQuery ? (
              <>
                <p>No servers match "{searchQuery}"</p>
                <p className="text-sm">Try a different search term</p>
              </>
            ) : (
              <>
                <p>No MCP servers found</p>
                <p className="text-sm">Check that example configurations exist in config/mcp-example-configs/</p>
              </>
            )}
          </div>
        )}
      </div>
      
      {allServerNames.size > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-700">
          <div className="flex items-center justify-between text-sm text-gray-400">
            <span>
              {searchQuery && filteredServerNames.length !== allServerNames.size
                ? `Showing ${filteredServerNames.length} of ${allServerNames.size} servers - `
                : ''
              }
              {Object.keys(activeServers).length} active, {Object.keys(availableServers).length} available
            </span>
            <span className="text-xs">
              Changes are applied immediately and trigger MCP reload
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

export default MCPServerManager