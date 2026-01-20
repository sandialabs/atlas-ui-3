import { useState, useCallback } from 'react'

/**
 * useServerAuthStatus
 *
 * Hook to manage MCP server authentication status.
 * Provides functions to fetch auth status, upload tokens, and remove tokens.
 *
 * Updated: 2025-01-20
 */
export function useServerAuthStatus() {
  const [authStatus, setAuthStatus] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  /**
   * Fetch authentication status for all servers
   * GET /api/mcp/auth/status
   * @returns {Promise<Object>} Map of server_name -> auth status
   */
  const fetchAuthStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/mcp/auth/status')
      if (!response.ok) {
        throw new Error(`Failed to fetch auth status: ${response.status}`)
      }
      const data = await response.json()

      // Convert array to map by server_name for easy lookup
      const statusMap = {}
      if (data.servers && Array.isArray(data.servers)) {
        data.servers.forEach(server => {
          statusMap[server.server_name] = server
        })
      }
      setAuthStatus(statusMap)
      return statusMap
    } catch (err) {
      console.error('Failed to fetch MCP auth status:', err)
      setError(err.message)
      return {}
    } finally {
      setLoading(false)
    }
  }, [])

  /**
   * Upload a token for a specific server
   * POST /api/mcp/auth/{serverName}/token
   * @param {string} serverName - The server to authenticate
   * @param {Object} tokenData - { token: string, expires_at?: number }
   * @returns {Promise<boolean>} Success status
   */
  const uploadToken = useCallback(async (serverName, tokenData) => {
    setError(null)
    try {
      const response = await fetch(`/api/mcp/auth/${serverName}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tokenData),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to upload token: ${response.status}`)
      }

      // Refresh status after successful upload
      await fetchAuthStatus()
      return true
    } catch (err) {
      console.error('Failed to upload token:', err)
      setError(err.message)
      throw err
    }
  }, [fetchAuthStatus])

  /**
   * Remove token for a specific server (disconnect)
   * DELETE /api/mcp/auth/{serverName}/token
   * @param {string} serverName - The server to disconnect
   * @returns {Promise<boolean>} Success status
   */
  const removeToken = useCallback(async (serverName) => {
    setError(null)
    try {
      const response = await fetch(`/api/mcp/auth/${serverName}/token`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to remove token: ${response.status}`)
      }

      // Refresh status after successful removal
      await fetchAuthStatus()
      return true
    } catch (err) {
      console.error('Failed to remove token:', err)
      setError(err.message)
      throw err
    }
  }, [fetchAuthStatus])

  /**
   * Get auth status for a specific server
   * @param {string} serverName - The server name to look up
   * @returns {Object|null} Server auth status or null if not found
   */
  const getServerAuth = useCallback((serverName) => {
    return authStatus[serverName] || null
  }, [authStatus])

  return {
    authStatus,
    loading,
    error,
    fetchAuthStatus,
    uploadToken,
    removeToken,
    getServerAuth
  }
}
