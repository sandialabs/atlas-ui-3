import { useState, useCallback, useEffect } from 'react'

/**
 * Loads the admin-preconfigured personas (issue #880).
 *
 * Personas are markdown files in a server-side folder; /api/personas returns
 * only the ones the caller's groups allow. They are read-only here: selecting
 * one sends its text as the turn's custom_system_prompt, exactly like a
 * user-authored prompt.
 */
export function usePersonas() {
  const [personas, setPersonas] = useState([])
  const [loading, setLoading] = useState(false)
  // Whether a fetch has ever come back successfully. Callers use this to tell
  // "no personas are configured" apart from "we have not asked yet", which
  // matters before acting on an empty list (see the stale-key effect in
  // ChatContext -- an initial empty list must not clear a selected persona).
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState(null)

  const fetchPersonas = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/personas')
      if (!res.ok) throw new Error(`Failed to load personas (${res.status})`)
      const data = await res.json()
      setPersonas(Array.isArray(data.personas) ? data.personas : [])
      setLoaded(true)
    } catch (e) {
      setError(e.message)
      setPersonas([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPersonas()
  }, [fetchPersonas])

  return { personas, loading, loaded, error, fetchPersonas }
}

export default usePersonas
