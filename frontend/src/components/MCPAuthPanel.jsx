import { useState, useEffect, useCallback } from 'react'
import {
  X,
  Shield,
  ShieldCheck,
  ShieldAlert,
  RefreshCw,
  Key,
  ExternalLink,
  Trash2,
  Upload,
  Clock,
  AlertCircle
} from 'lucide-react'

/**
 * MCP Authentication Panel
 *
 * Allows users to manage their authentication with MCP servers that require OAuth or JWT.
 * Shows auth status, allows OAuth login, JWT upload, and token removal.
 *
 * Updated: 2025-01-19
 */
const MCPAuthPanel = ({ isOpen, onClose }) => {
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedServer, setSelectedServer] = useState(null)
  const [jwtInput, setJwtInput] = useState('')
  const [jwtExpiry, setJwtExpiry] = useState('')
  const [showJwtModal, setShowJwtModal] = useState(false)
  const [actionLoading, setActionLoading] = useState(null)

  // Fetch auth status from backend
  const fetchAuthStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/mcp/auth/status')
      if (!response.ok) {
        throw new Error(`Failed to fetch auth status: ${response.status}`)
      }
      const data = await response.json()
      setServers(data.servers || [])
    } catch (err) {
      console.error('Failed to fetch MCP auth status:', err)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Listen for OAuth callback messages
  useEffect(() => {
    const handleOAuthMessage = (event) => {
      if (event.data?.type === 'oauth_success') {
        // Refresh auth status after successful OAuth
        fetchAuthStatus()
      } else if (event.data?.type === 'oauth_error') {
        setError(`OAuth failed for ${event.data.server}: ${event.data.error}`)
      }
    }

    window.addEventListener('message', handleOAuthMessage)
    return () => window.removeEventListener('message', handleOAuthMessage)
  }, [fetchAuthStatus])

  // Fetch status when panel opens
  useEffect(() => {
    if (isOpen) {
      fetchAuthStatus()
    }
  }, [isOpen, fetchAuthStatus])

  // Start OAuth flow
  const handleStartOAuth = async (serverName) => {
    setActionLoading(serverName)
    setError(null)
    try {
      const response = await fetch(`/api/mcp/auth/${serverName}/oauth/start`)
      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to start OAuth: ${response.status}`)
      }
      const data = await response.json()

      // Open authorization URL in a popup window
      const width = 600
      const height = 700
      const left = window.screenX + (window.outerWidth - width) / 2
      const top = window.screenY + (window.outerHeight - height) / 2

      window.open(
        data.authorization_url,
        `oauth_${serverName}`,
        `width=${width},height=${height},left=${left},top=${top},popup=yes`
      )
    } catch (err) {
      console.error('Failed to start OAuth:', err)
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  // Upload JWT token
  const handleUploadJwt = async () => {
    if (!selectedServer || !jwtInput.trim()) return

    setActionLoading(selectedServer)
    setError(null)
    try {
      const body = {
        token: jwtInput.trim(),
      }

      // Parse expiry date if provided
      if (jwtExpiry) {
        const expiryDate = new Date(jwtExpiry)
        if (!isNaN(expiryDate.getTime())) {
          body.expires_at = expiryDate.getTime() / 1000  // Convert to Unix timestamp
        }
      }

      const response = await fetch(`/api/mcp/auth/${selectedServer}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to upload token: ${response.status}`)
      }

      // Clear form and close modal
      setJwtInput('')
      setJwtExpiry('')
      setShowJwtModal(false)
      setSelectedServer(null)

      // Refresh status
      await fetchAuthStatus()
    } catch (err) {
      console.error('Failed to upload JWT:', err)
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  // Remove token (disconnect)
  const handleDisconnect = async (serverName) => {
    if (!window.confirm(`Remove authentication for "${serverName}"? You will need to re-authenticate to use this server's tools.`)) {
      return
    }

    setActionLoading(serverName)
    setError(null)
    try {
      const response = await fetch(`/api/mcp/auth/${serverName}/token`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to remove token: ${response.status}`)
      }

      // Refresh status
      await fetchAuthStatus()
    } catch (err) {
      console.error('Failed to disconnect:', err)
      setError(err.message)
    } finally {
      setActionLoading(null)
    }
  }

  // Format time until expiry
  const formatTimeUntilExpiry = (seconds) => {
    if (seconds === null || seconds === undefined) return 'Never'
    if (seconds <= 0) return 'Expired'

    const hours = Math.floor(seconds / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)

    if (hours > 24) {
      const days = Math.floor(hours / 24)
      return `${days}d ${hours % 24}h`
    }
    if (hours > 0) {
      return `${hours}h ${minutes}m`
    }
    return `${minutes}m`
  }

  // Get servers that require auth
  const authRequiredServers = servers.filter(s => s.auth_required)

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-700 flex-shrink-0">
          <div className="flex items-center gap-3">
            <Shield className="w-6 h-6 text-blue-400" />
            <h2 className="text-xl font-semibold text-gray-100">MCP Server Authentication</h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6">
          {/* Error Display */}
          {error && (
            <div className="mb-4 p-4 bg-red-900/30 border border-red-700 rounded-lg flex items-center gap-3">
              <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
              <p className="text-red-300 text-sm">{error}</p>
              <button
                onClick={() => setError(null)}
                className="ml-auto text-red-400 hover:text-red-300"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Loading State */}
          {loading && (
            <div className="flex items-center justify-center py-12">
              <RefreshCw className="w-8 h-8 text-blue-400 animate-spin" />
            </div>
          )}

          {/* No Auth Required Servers */}
          {!loading && authRequiredServers.length === 0 && (
            <div className="text-center py-12">
              <ShieldCheck className="w-16 h-16 text-gray-600 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-gray-300 mb-2">No Authentication Required</h3>
              <p className="text-gray-400">
                None of your enabled MCP servers require user authentication.
              </p>
            </div>
          )}

          {/* Server List */}
          {!loading && authRequiredServers.length > 0 && (
            <div className="space-y-4">
              <p className="text-sm text-gray-400 mb-4">
                The following MCP servers require you to authenticate before you can use their tools.
              </p>

              {authRequiredServers.map((server) => (
                <div
                  key={server.server_name}
                  className={`p-4 rounded-lg border transition-colors ${
                    server.authenticated && !server.is_expired
                      ? 'bg-green-900/20 border-green-700'
                      : server.is_expired
                      ? 'bg-yellow-900/20 border-yellow-700'
                      : 'bg-gray-700/50 border-gray-600'
                  }`}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-3">
                      {server.authenticated && !server.is_expired ? (
                        <ShieldCheck className="w-5 h-5 text-green-400 mt-0.5" />
                      ) : server.is_expired ? (
                        <ShieldAlert className="w-5 h-5 text-yellow-400 mt-0.5" />
                      ) : (
                        <Shield className="w-5 h-5 text-gray-400 mt-0.5" />
                      )}
                      <div>
                        <h4 className="font-medium text-gray-100">{server.server_name}</h4>
                        <p className="text-sm text-gray-400">{server.description}</p>
                        <div className="flex items-center gap-2 mt-2">
                          <span className={`text-xs px-2 py-0.5 rounded ${
                            server.auth_type === 'oauth'
                              ? 'bg-blue-600/30 text-blue-300'
                              : 'bg-purple-600/30 text-purple-300'
                          }`}>
                            {server.auth_type.toUpperCase()}
                          </span>
                          {server.authenticated && (
                            <>
                              <span className="text-xs text-gray-500">|</span>
                              <span className="text-xs text-gray-400 flex items-center gap-1">
                                <Clock className="w-3 h-3" />
                                Expires: {formatTimeUntilExpiry(server.time_until_expiry)}
                              </span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-2">
                      {server.authenticated ? (
                        <button
                          onClick={() => handleDisconnect(server.server_name)}
                          disabled={actionLoading === server.server_name}
                          className="flex items-center gap-2 px-3 py-1.5 rounded bg-red-600/20 text-red-400 hover:bg-red-600/30 transition-colors text-sm disabled:opacity-50"
                        >
                          {actionLoading === server.server_name ? (
                            <RefreshCw className="w-4 h-4 animate-spin" />
                          ) : (
                            <Trash2 className="w-4 h-4" />
                          )}
                          Disconnect
                        </button>
                      ) : server.auth_type === 'oauth' ? (
                        <button
                          onClick={() => handleStartOAuth(server.server_name)}
                          disabled={actionLoading === server.server_name}
                          className="flex items-center gap-2 px-3 py-1.5 rounded bg-blue-600 text-white hover:bg-blue-700 transition-colors text-sm disabled:opacity-50"
                        >
                          {actionLoading === server.server_name ? (
                            <RefreshCw className="w-4 h-4 animate-spin" />
                          ) : (
                            <ExternalLink className="w-4 h-4" />
                          )}
                          Connect
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            setSelectedServer(server.server_name)
                            setShowJwtModal(true)
                          }}
                          disabled={actionLoading === server.server_name}
                          className="flex items-center gap-2 px-3 py-1.5 rounded bg-purple-600 text-white hover:bg-purple-700 transition-colors text-sm disabled:opacity-50"
                        >
                          {actionLoading === server.server_name ? (
                            <RefreshCw className="w-4 h-4 animate-spin" />
                          ) : (
                            <Key className="w-4 h-4" />
                          )}
                          Add Token
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-700 flex-shrink-0">
          <button
            onClick={fetchAuthStatus}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            Close
          </button>
        </div>
      </div>

      {/* JWT Upload Modal */}
      {showJwtModal && (
        <div
          className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-60"
          onClick={() => {
            setShowJwtModal(false)
            setSelectedServer(null)
            setJwtInput('')
            setJwtExpiry('')
          }}
        >
          <div
            className="bg-gray-800 rounded-lg shadow-xl max-w-lg w-full mx-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold text-gray-100 mb-4 flex items-center gap-2">
              <Upload className="w-5 h-5" />
              Upload Token for {selectedServer}
            </h3>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  JWT / Bearer Token
                </label>
                <textarea
                  value={jwtInput}
                  onChange={(e) => setJwtInput(e.target.value)}
                  placeholder="Paste your JWT or bearer token here..."
                  className="w-full px-3 py-2 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 h-32 font-mono text-sm"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Expiration (optional)
                </label>
                <input
                  type="datetime-local"
                  value={jwtExpiry}
                  onChange={(e) => setJwtExpiry(e.target.value)}
                  className="w-full px-3 py-2 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="text-xs text-gray-400 mt-1">
                  Set when the token expires. Leave empty if the token doesn't expire.
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setShowJwtModal(false)
                  setSelectedServer(null)
                  setJwtInput('')
                  setJwtExpiry('')
                }}
                className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUploadJwt}
                disabled={!jwtInput.trim() || actionLoading === selectedServer}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50"
              >
                {actionLoading === selectedServer ? (
                  <RefreshCw className="w-4 h-4 animate-spin" />
                ) : (
                  <Upload className="w-4 h-4" />
                )}
                Upload Token
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default MCPAuthPanel
