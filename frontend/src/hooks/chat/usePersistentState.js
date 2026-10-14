import { useCallback, useEffect, useState } from 'react'

function readStored(key, initialValue) {
  if (key === null) return initialValue
  try {
    const raw = localStorage.getItem(key)
    return raw !== null ? JSON.parse(raw) : initialValue
  } catch {
    return initialValue
  }
}

// State mirrored to localStorage under `key`. When `key` changes (e.g. a
// per-user key once the user is known) the value is re-read from the new key
// rather than carried over to it. A null key keeps the value in memory only.
// Only values set through the setter are written back.
export function usePersistentState(key, initialValue) {
  const [state, setState] = useState(() => ({ key, value: readStored(key, initialValue), dirty: false }))

  let current = state
  if (state.key !== key) {
    current = { key, value: readStored(key, initialValue), dirty: false }
    setState(current)
  }

  const setValue = useCallback(next => {
    setState(prev => ({
      key: prev.key,
      value: typeof next === 'function' ? next(prev.value) : next,
      dirty: true,
    }))
  }, [])

  useEffect(() => {
    if (!state.dirty || state.key === null) return
    try {
      localStorage.setItem(state.key, JSON.stringify(state.value))
    } catch {
      /* ignore */
    }
  }, [state])

  return [current.value, setValue]
}
