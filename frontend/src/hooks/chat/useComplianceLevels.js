import { useEffect, useState } from 'react'

/**
 * Compliance level definitions from /api/compliance-levels.
 *
 * Lives in the chat context (not the marketplace) because the chat context
 * owns the selections the compliance filter has to prune and the payload it
 * has to gate; the marketplace reads the same list from useChat().
 */
export function useComplianceLevels(enabled) {
  const [complianceLevels, setComplianceLevels] = useState([])
  const [complianceMode, setComplianceMode] = useState('explicit_allowlist')

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    fetch('/api/compliance-levels')
      .then(res => res.json())
      .then(data => {
        if (cancelled) return
        setComplianceLevels(Array.isArray(data?.levels) ? data.levels : [])
        setComplianceMode(data?.mode || 'explicit_allowlist')
      })
      .catch(err => console.error('Failed to load compliance levels:', err))
    return () => { cancelled = true }
  }, [enabled])

  return { complianceLevels, complianceMode }
}
