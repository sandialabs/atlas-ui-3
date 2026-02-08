import { useState, useCallback } from 'react'

/**
 * useLLMAuthStatus
 *
 * Hook to manage per-user LLM API key authentication status.
 * Mirrors useServerAuthStatus but targets /api/llm/auth/ endpoints.
 *
 * Updated: 2026-02-08
 */
export function useLLMAuthStatus() {
  const [authStatus, setAuthStatus] = useState({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  /**
   * Fetch authentication status for all models with api_key_source: "user"
   * GET /api/llm/auth/status
   * @returns {Promise<Object>} Map of model_name -> auth status
   */
  const fetchAuthStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/llm/auth/status')
      if (!response.ok) {
        throw new Error(`Failed to fetch LLM auth status: ${response.status}`)
      }
      const data = await response.json()

      const statusMap = {}
      if (data.models && Array.isArray(data.models)) {
        data.models.forEach(model => {
          statusMap[model.model_name] = model
        })
      }
      setAuthStatus(statusMap)
      return statusMap
    } catch (err) {
      console.error('Failed to fetch LLM auth status:', err)
      setError(err.message)
      return {}
    } finally {
      setLoading(false)
    }
  }, [])

  /**
   * Upload an API key for a specific model
   * POST /api/llm/auth/{modelName}/token
   * @param {string} modelName - The model to authenticate
   * @param {Object} tokenData - { token: string, expires_at?: number }
   * @returns {Promise<boolean>} Success status
   */
  const uploadToken = useCallback(async (modelName, tokenData) => {
    setError(null)
    try {
      const response = await fetch(`/api/llm/auth/${encodeURIComponent(modelName)}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(tokenData),
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to upload API key: ${response.status}`)
      }

      await fetchAuthStatus()
      return true
    } catch (err) {
      console.error('Failed to upload LLM API key:', err)
      setError(err.message)
      throw err
    }
  }, [fetchAuthStatus])

  /**
   * Remove API key for a specific model
   * DELETE /api/llm/auth/{modelName}/token
   * @param {string} modelName - The model to remove key for
   * @returns {Promise<boolean>} Success status
   */
  const removeToken = useCallback(async (modelName) => {
    setError(null)
    try {
      const response = await fetch(`/api/llm/auth/${encodeURIComponent(modelName)}/token`, {
        method: 'DELETE',
      })

      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || `Failed to remove API key: ${response.status}`)
      }

      await fetchAuthStatus()
      return true
    } catch (err) {
      console.error('Failed to remove LLM API key:', err)
      setError(err.message)
      throw err
    }
  }, [fetchAuthStatus])

  /**
   * Get auth status for a specific model
   * @param {string} modelName - The model name to look up
   * @returns {Object|null} Model auth status or null
   */
  const getModelAuth = useCallback((modelName) => {
    return authStatus[modelName] || null
  }, [authStatus])

  return {
    authStatus,
    loading,
    error,
    fetchAuthStatus,
    uploadToken,
    removeToken,
    getModelAuth,
  }
}
