import { useState, useCallback } from 'react'

/**
 * useCaptureConsent
 *
 * Manages the user's opt-in consent for the fine-tune capture feature (issue
 * #622). Consent state is stored BACKEND-side (not localStorage), so this hook
 * fetches it from the API and POSTs changes. It also exposes a "delete my
 * captured data" action.
 *
 * Endpoints:
 *   GET    /api/capture/consent -> consent state
 *   POST   /api/capture/consent { enabled } -> updated consent state
 *   DELETE /api/capture/me -> { deleted_records, files_touched }
 *
 * Consent state shape:
 *   { system_enabled, user_enabled, consent_version, current_consent_version,
 *     consented_at, needs_reconsent }
 */
export function useCaptureConsent() {
  const [consent, setConsent] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Fetch current consent state for the signed-in user.
  const fetchConsent = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('/api/capture/consent')
      if (!response.ok) {
        throw new Error(`Failed to fetch capture consent: ${response.status}`)
      }
      const data = await response.json()
      setConsent(data)
      return data
    } catch (err) {
      console.error('Failed to fetch capture consent:', err)
      setError(err.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  // Enable or disable capture. The backend returns 409 if enabling while the
  // system is disabled; surface that as a friendly error instead of throwing.
  const setEnabled = useCallback(async (enabled) => {
    setSaving(true)
    setError(null)
    try {
      const response = await fetch('/api/capture/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
      if (response.status === 409) {
        setError('Capture is disabled by your administrator.')
        // Refresh so the toggle reflects the authoritative server state.
        await fetchConsent()
        return null
      }
      if (!response.ok) {
        throw new Error(`Failed to update capture consent: ${response.status}`)
      }
      const data = await response.json()
      setConsent(data)
      return data
    } catch (err) {
      console.error('Failed to update capture consent:', err)
      setError(err.message)
      return null
    } finally {
      setSaving(false)
    }
  }, [fetchConsent])

  // Delete all captured data for the current user. Returns the counts the
  // backend reports so the UI can confirm what was removed.
  const deleteMyData = useCallback(async () => {
    setError(null)
    try {
      const response = await fetch('/api/capture/me', { method: 'DELETE' })
      if (!response.ok) {
        throw new Error(`Failed to delete captured data: ${response.status}`)
      }
      return await response.json()
    } catch (err) {
      console.error('Failed to delete captured data:', err)
      setError(err.message)
      throw err
    }
  }, [])

  return {
    consent,
    loading,
    saving,
    error,
    fetchConsent,
    setEnabled,
    deleteMyData,
    systemEnabled: consent?.system_enabled ?? false,
    userEnabled: consent?.user_enabled ?? false,
  }
}
