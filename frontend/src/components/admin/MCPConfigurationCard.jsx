import React, { useEffect, useState, useRef } from 'react'
import { Settings, RefreshCw, RotateCcw, Activity } from 'lucide-react'
import { useChat } from '../../contexts/ChatContext'
import { calculateBackoffDelay } from '../../hooks/usePollingWithBackoff'

// Polling configuration
const NORMAL_POLLING_INTERVAL = 30000 // 30 seconds (increased from 15s)
const MAX_BACKOFF_DELAY = 300000 // 5 minutes (increased from 30s)

const MCPConfigurationCard = ({ openModal, addNotification, systemStatus }) => {
  const { refreshConfig } = useChat()
  const [mcpStatus, setMcpStatus] = useState({
    connected_servers: [],
    failed_servers: {},
  })
  const [statusLoading, setStatusLoading] = useState(false)
  const [reloadLoading, setReloadLoading] = useState(false)
  const [reconnectLoading, setReconnectLoading] = useState(false)

  // Refs for polling state (avoid re-renders and stale closures)
  const failureCountRef = useRef(0)
  const timeoutIdRef = useRef(null)
  const isMountedRef = useRef(true)
  const pollFnRef = useRef(null)

  // Manual refresh function for button clicks (doesn't affect polling schedule)
  const loadMCPStatus = async () => {
    try {
      setStatusLoading(true)
      const response = await fetch('/admin/mcp/status')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      const configured = new Set(data.configured_servers || [])
      const freshConnected = (data.connected_servers || []).filter((name) => configured.has(name))
      const freshFailedEntries = Object.entries(data.failed_servers || {}).filter(
        ([name]) => configured.has(name)
      )
      setMcpStatus({
        connected_servers: freshConnected,
        failed_servers: Object.fromEntries(freshFailedEntries),
      })
    } catch (err) {
      console.error('Error loading MCP status for card:', err)
    } finally {
      setStatusLoading(false)
    }
  }

  useEffect(() => {
    isMountedRef.current = true

    const scheduleNextPoll = (delay) => {
      if (!isMountedRef.current) return

      // Clear any existing timeout
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current)
      }

      timeoutIdRef.current = setTimeout(() => {
        if (isMountedRef.current && pollFnRef.current) {
          pollFnRef.current()
        }
      }, delay)
    }

    const pollMCPStatus = async () => {
      try {
        setStatusLoading(true)
        const response = await fetch('/admin/mcp/status')
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const data = await response.json()
        // Normalize status so we don't show stale servers that no longer exist
        const configured = new Set(data.configured_servers || [])

        const freshConnected = (data.connected_servers || []).filter((name) => configured.has(name))

        const freshFailedEntries = Object.entries(data.failed_servers || {}).filter(
          ([name]) => configured.has(name)
        )

        setMcpStatus({
          connected_servers: freshConnected,
          failed_servers: Object.fromEntries(freshFailedEntries),
        })

        // Reset failure count on success and schedule next poll at normal interval
        failureCountRef.current = 0
        scheduleNextPoll(NORMAL_POLLING_INTERVAL)
      } catch (err) {
        // Keep this quiet in the UI but log to console for debugging
        console.error('Error loading MCP status for card:', err)

        // Increment failure count and schedule next poll with backoff (includes jitter)
        failureCountRef.current += 1
        const delay = calculateBackoffDelay(failureCountRef.current, 1000, MAX_BACKOFF_DELAY)
        scheduleNextPoll(delay)
      } finally {
        setStatusLoading(false)
      }
    }

    // Store ref so timeout can call the latest version
    pollFnRef.current = pollMCPStatus

    // Initial load
    pollMCPStatus()

    // Cleanup on unmount
    return () => {
      isMountedRef.current = false
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current)
      }
    }
  }, [])

  const manageMCP = async () => {
    try {
      const response = await fetch('/admin/config/view')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()

      const mcpConfigJson = JSON.stringify(data.mcp_config || {}, null, 2)

      openModal('View MCP Configuration', {
        type: 'textarea',
        value: mcpConfigJson,
        readOnly: true,
        description: 'Current MCP servers and their properties, as loaded from config/mcp.json. To make changes, edit that file directly and then use the controls below to hot-reload.',
      }, 'mcp-config')
    } catch (err) {
      addNotification('Error loading MCP configuration: ' + err.message, 'error')
    }
  }

  const viewMCPStatus = async () => {
    try {
      const response = await fetch('/admin/mcp/status')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()

      openModal('MCP Status', {
        type: 'textarea',
        value: JSON.stringify(data, null, 2),
        description: 'You don’t need to restart after editing mcp.json.\n\n- POST /admin/mcp/reload applies config changes and rediscover tools/prompts.\n- GET /admin/mcp/status shows which servers are connected or failing.\n- POST /admin/mcp/reconnect (plus the auto-reconnect feature flag) retries failed servers with exponential backoff.'
      })
    } catch (err) {
      addNotification('Error loading MCP status: ' + err.message, 'error')
    }
  }

  const reloadMCP = async () => {
    try {
      setReloadLoading(true)
      const response = await fetch('/admin/mcp/reload', { method: 'POST' })
      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`)
      }

      addNotification(`MCP reload completed: ${data.servers.length} servers loaded, ${data.failed_servers.length} failed`, 'success')
      // Refresh inline status after reload
      loadMCPStatus()
      // Refresh the main config to update tools list in sidebar
      if (refreshConfig) {
        refreshConfig().catch(err => {
          console.error('Failed to refresh config:', err)
        })
      }
    } catch (err) {
      addNotification('Error reloading MCP servers: ' + err.message, 'error')
    } finally {
      setReloadLoading(false)
    }
  }

  const reconnectMCP = async () => {
    try {
      setReconnectLoading(true)
      const response = await fetch('/admin/mcp/reconnect', { method: 'POST' })
      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`)
      }

      const attempted = data.result?.attempted?.length || 0
      const reconnected = data.result?.reconnected?.length || 0
      const stillFailed = data.result?.still_failed?.length || 0
      addNotification(`MCP reconnect: attempted ${attempted}, reconnected ${reconnected}, still failing ${stillFailed}`, 'success')
      // Refresh inline status after reconnect
      loadMCPStatus()
      // Refresh the main config to update tools list in sidebar
      if (refreshConfig) {
        refreshConfig().catch(err => {
          console.error('Failed to refresh config:', err)
        })
      }
    } catch (err) {
      addNotification('Error reconnecting MCP servers: ' + err.message, 'error')
    } finally {
      setReconnectLoading(false)
    }
  }

  const getStatusColor = (status) => {
    switch (status) {
      case 'healthy': return 'text-green-400 bg-green-900/20'
      case 'warning': return 'text-yellow-400 bg-yellow-900/20'
      case 'error': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <Settings className="w-6 h-6 text-purple-400" />
        <h2 className="text-lg font-semibold">MCP Configuration & Controls</h2>
      </div>
      <p className="text-gray-400 mb-4">Configure MCP servers and manage hot reload and reconnect.</p>
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor(systemStatus.overall_status || 'healthy')}`}>
        {statusLoading ? 'Updating MCP status…' : (systemStatus.overall_status || 'Ready')}
      </div>
      {/* Inline MCP server status */}
      <div className="mb-4 space-y-2 text-sm">
        {mcpStatus.connected_servers.length > 0 && (
          <div>
            <div className="text-gray-400 mb-1">Connected servers</div>
            <div className="flex flex-wrap gap-1">
              {mcpStatus.connected_servers.map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-green-900/40 text-green-300 border border-green-700/60"
                >
                  ● {name}
                </span>
              ))}
            </div>
          </div>
        )}
        {Object.keys(mcpStatus.failed_servers).length > 0 && (
          <div>
            <div className="text-gray-400 mb-1">Failed servers</div>
            <div className="flex flex-wrap gap-1">
              {Object.entries(mcpStatus.failed_servers).map(([name, info]) => (
                <span
                  key={name}
                  className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-red-900/40 text-red-300 border border-red-700/60"
                  title={info?.error || 'Failed to connect'}
                >
                  ● {name}
                </span>
              ))}
            </div>
          </div>
        )}
        {mcpStatus.connected_servers.length === 0 && Object.keys(mcpStatus.failed_servers).length === 0 && (
          <div className="text-gray-500 text-xs">No MCP status available yet.</div>
        )}
      </div>
      <div className="space-y-2">
        <button 
          onClick={manageMCP}
          className="w-full px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors flex items-center justify-center gap-2"
        >
          <Settings className="w-4 h-4" />
          View MCP Config
        </button>
        <button
          onClick={viewMCPStatus}
          className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors flex items-center justify-center gap-2"
        >
          <Activity className="w-4 h-4" />
          View MCP Status
        </button>
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={reloadMCP}
            disabled={reloadLoading}
            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-900 disabled:opacity-60 rounded-lg text-sm flex items-center justify-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${reloadLoading ? 'animate-spin' : ''}`} />
            {reloadLoading ? 'Reloading and reconnecting…' : 'Reload and reconnect all MCP servers'}
          </button>
          <button
            onClick={reconnectMCP}
            disabled={reconnectLoading}
            className="px-3 py-2 bg-sky-600 hover:bg-sky-700 disabled:bg-sky-900 disabled:opacity-60 rounded-lg text-sm flex items-center justify-center gap-2"
          >
            <RotateCcw className={`w-4 h-4 ${reconnectLoading ? 'animate-spin' : ''}`} />
            {reconnectLoading ? 'Reconnecting failed servers…' : 'Reconnect disconnected MCP servers only'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default MCPConfigurationCard
