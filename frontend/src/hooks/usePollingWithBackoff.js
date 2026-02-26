import { useEffect, useRef, useCallback } from 'react'

/**
 * Adds jitter to a delay to prevent thundering herd when multiple
 * clients back off simultaneously. Returns delay +/- up to 20%.
 */
function addJitter(delay) {
  const jitterFactor = 0.8 + Math.random() * 0.4 // 0.8 to 1.2
  return Math.round(delay * jitterFactor)
}

/**
 * Calculates exponential backoff delay based on consecutive failure count.
 *
 * @param {number} failures - Number of consecutive failures
 * @param {number} baseDelay - Starting delay in ms (default 1000)
 * @param {number} maxDelay - Maximum backoff delay in ms (default 300000 = 5 min)
 * @returns {number} Delay in ms with jitter applied
 */
export function calculateBackoffDelay(failures, baseDelay = 1000, maxDelay = 300000) {
  if (failures <= 0) return 0
  const raw = Math.min(baseDelay * Math.pow(2, failures - 1), maxDelay)
  return addJitter(raw)
}

/**
 * Hook that polls a function at a regular interval, switching to exponential
 * backoff with jitter when errors occur.
 *
 * @param {Function} fetchFn - Async function to poll. Must throw on failure.
 * @param {Object} options
 * @param {number} options.normalInterval - Interval in ms when healthy (default 60000)
 * @param {number} options.maxBackoffDelay - Max backoff delay in ms (default 300000)
 * @param {boolean} options.enabled - Whether polling is active (default true)
 * @param {Array} options.deps - Additional dependency array items that should restart polling
 */
export function usePollingWithBackoff(fetchFn, {
  normalInterval = 60000,
  maxBackoffDelay = 300000,
  enabled = true,
  deps = [],
} = {}) {
  const failureCountRef = useRef(0)
  const timeoutIdRef = useRef(null)
  const isMountedRef = useRef(true)
  const pollFnRef = useRef(null)
  const fetchFnRef = useRef(fetchFn)
  // Keep fetchFn ref current so scheduled polls always call the latest version
  fetchFnRef.current = fetchFn

  const resetBackoff = useCallback(() => {
    failureCountRef.current = 0
  }, [])

  useEffect(() => {
    isMountedRef.current = true

    const scheduleNext = (delay) => {
      if (!isMountedRef.current) return
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current)
      timeoutIdRef.current = setTimeout(() => {
        if (isMountedRef.current && pollFnRef.current) {
          pollFnRef.current()
        }
      }, delay)
    }

    const poll = async () => {
      if (!isMountedRef.current || !enabled) return
      try {
        await fetchFnRef.current()
        failureCountRef.current = 0
        scheduleNext(normalInterval)
      } catch {
        failureCountRef.current += 1
        const delay = calculateBackoffDelay(
          failureCountRef.current,
          1000,
          maxBackoffDelay
        )
        scheduleNext(delay)
      }
    }

    pollFnRef.current = poll

    if (enabled) {
      poll()
    }

    return () => {
      isMountedRef.current = false
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, normalInterval, maxBackoffDelay, ...deps])

  return { resetBackoff }
}
