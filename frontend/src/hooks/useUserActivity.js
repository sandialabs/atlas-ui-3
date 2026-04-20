import { useEffect, useRef, useState } from 'react'

const DEFAULT_ACTIVITY_EVENTS = [
  'mousemove',
  'mousedown',
  'keydown',
  'touchstart',
  'scroll',
  'wheel',
]

/**
 * Tracks whether there has been recent user activity (mouse, keyboard, touch,
 * scroll) within `idleTimeoutMs`. Returns `true` while the user is active and
 * flips to `false` after the idle timeout elapses with no activity. The first
 * activity event after going idle flips the value back to `true` immediately.
 *
 * Intended to gate background polling so the backend is not hit while the user
 * is away from their machine. Consumers typically pass the returned value as
 * the `enabled` option of `usePollingWithBackoff`, which will trigger an
 * immediate poll when the user returns.
 *
 * @param {Object} [options]
 * @param {number} [options.idleTimeoutMs=300000] Milliseconds without activity
 *   before the user is considered idle. Defaults to 5 minutes.
 * @param {string[]} [options.events] DOM events that count as activity.
 * @returns {boolean} `true` if the user is currently active, else `false`.
 */
export function useUserActivity({
  idleTimeoutMs = 5 * 60 * 1000,
  events = DEFAULT_ACTIVITY_EVENTS,
} = {}) {
  const [isActive, setIsActive] = useState(true)
  const timeoutIdRef = useRef(null)
  const isActiveRef = useRef(true)

  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    const scheduleIdle = () => {
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current)
      timeoutIdRef.current = setTimeout(() => {
        isActiveRef.current = false
        setIsActive(false)
      }, idleTimeoutMs)
    }

    const handleActivity = () => {
      if (!isActiveRef.current) {
        isActiveRef.current = true
        setIsActive(true)
      }
      scheduleIdle()
    }

    // Start the idle countdown so long-idle sessions eventually pause even
    // without any initial events.
    scheduleIdle()

    const listenerOpts = { passive: true }
    events.forEach((event) => {
      window.addEventListener(event, handleActivity, listenerOpts)
    })

    return () => {
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current)
      events.forEach((event) => {
        window.removeEventListener(event, handleActivity, listenerOpts)
      })
    }
    // `events` is expected to be stable; re-running on identity change would
    // needlessly re-bind listeners. Intentionally depend only on idleTimeoutMs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idleTimeoutMs])

  return isActive
}
