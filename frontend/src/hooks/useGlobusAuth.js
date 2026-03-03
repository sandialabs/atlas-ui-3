import { useState, useCallback } from 'react'

/**
 * useGlobusAuth
 *
 * Hook to manage Globus OAuth authentication status for ALCF and other
 * Globus-scoped services. Tokens are obtained via OAuth redirect flow
 * (not manual input) and stored server-side in MCPTokenStorage.
 *
 * Updated: 2026-02-24
 */
export function useGlobusAuth() {
  const [authStatus, setAuthStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  /**
   * Fetch Globus authentication status for the current user.
   * GET /api/globus/status
   * @returns {Promise<Object>} Globus auth status
   */
  const fetchAuthStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/globus/status')
      if (!response.ok) {
        throw new Error(`Failed to fetch Globus auth status: ${response.status}`)
      }
      const data = await response.json()
      setAuthStatus(data)
      return data
    } catch (err) {
      console.error('Failed to fetch Globus auth status:', err)
      setError(err.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  /**
   * Initiate Globus OAuth login flow.
   * Redirects the browser to /auth/globus/login which starts the OAuth flow.
   */
  const login = useCallback(() => {
    window.location.href = '/auth/globus/login'
  }, [])

  /**
   * Log out from Globus and remove all stored tokens.
   * DELETE /api/globus/tokens then refresh status.
   * @returns {Promise<boolean>} Success status
   */
  const logout = useCallback(async () => {
    setError(null)
    try {
      const response = await fetch('/api/globus/tokens', {
        method: 'DELETE',
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to remove Globus tokens: ${response.status}`)
      }

      await fetchAuthStatus()
      return true
    } catch (err) {
      console.error('Failed to remove Globus tokens:', err)
      setError(err.message)
      throw err
    }
  }, [fetchAuthStatus])

  /**
   * Check if the user has a valid token for a specific Globus resource server.
   * @param {string} resourceServer - The resource server UUID to check
   * @returns {boolean} Whether a valid token exists
   */
  const hasValidToken = useCallback((resourceServer) => {
    if (!authStatus || !authStatus.resource_servers) return false
    return authStatus.resource_servers.some(
      rs => rs.resource_server === resourceServer && !rs.is_expired
    )
  }, [authStatus])

  return {
    authStatus,
    loading,
    error,
    fetchAuthStatus,
    login,
    logout,
    hasValidToken,
    isAuthenticated: authStatus?.authenticated ?? false,
    isEnabled: authStatus?.enabled ?? false,
  }
}
