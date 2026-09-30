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
 * The poll is scoped to one effect generation. When `enabled` or a dep
 * changes, the old generation is cancelled and a fresh one starts; a request
 * still in the air from the old generation can neither schedule the new
 * generation's next poll nor hold its in-flight guard, so switching from one
 * polled subject to another does not stall the new subject behind the old
 * request.
 *
 * @param {Function} fetchFn - Async function to poll. Must throw on failure.
 * @param {Object} options
 * @param {number} options.normalInterval - Interval in ms when healthy (default 60000)
 * @param {number} options.maxBackoffDelay - Max backoff delay in ms (default 300000)
 * @param {number} options.backoffBase - First failure delay in ms (default 1000).
 *   A fast-interval poller should pass its normalInterval here so a failing
 *   server is not retried faster than the healthy cadence; the scheduled
 *   backoff delay is floored at this value (clamped by maxBackoffDelay), so
 *   jitter cannot pull a retry below it.
 * @param {boolean} options.enabled - Whether polling is active (default true).
 *   Re-enabling resumes rather than immediate-polling when a backoff is still
 *   pending, so a failing endpoint is not hit on every idle/active flip.
 * @param {boolean} options.pauseWhenHidden - Suspend polling while the tab is
 *   hidden (default false); polling resumes when it becomes visible, honoring
 *   any in-progress backoff delay rather than polling immediately.
 * @param {Array} options.deps - Additional dependency array items that
 *   identify the polled subject; a change resets the failure count. Compared
 *   element-wise with Object.is, so functions and undefined are fine.
 */
export function usePollingWithBackoff(fetchFn, {
  normalInterval = 60000,
  maxBackoffDelay = 300000,
  backoffBase = 1000,
  enabled = true,
  pauseWhenHidden = false,
  deps = [],
} = {}) {
  const failureCountRef = useRef(0)
  const timeoutIdRef = useRef(null)
  const generationRef = useRef(0)
  const fetchFnRef = useRef(fetchFn)
  // Keep fetchFn ref current so scheduled polls always call the latest version
  fetchFnRef.current = fetchFn
  // The deps that identify the polled subject. The failure count and the
  // pending retry reset only when these change, not when `enabled` toggles: an
  // inactive/active flip is the same subject and keeps its backoff, so a
  // failing endpoint is not hammered afresh on every resume.
  const depsRef = useRef(deps)
  // When the next backoff retry is due (epoch ms). A ref so it survives an
  // `enabled` toggle: re-enabling resumes after the remaining delay instead of
  // polling immediately. 0 means no backoff is pending.
  const retryAtRef = useRef(0)

  const resetBackoff = useCallback(() => {
    failureCountRef.current = 0
    retryAtRef.current = 0
  }, [])

  useEffect(() => {
    // Each effect run is a generation. A poll or a schedule that belongs to a
    // superseded generation must not run, and must not touch this generation's
    // in-flight guard -- the two are per-generation closures, not shared refs.
    const generation = generationRef.current + 1
    generationRef.current = generation
    // A new polled subject starts healthy; a failure from the subject this
    // generation replaced must not make the new one back off immediately. Only
    // a deps change is a new subject -- `enabled` toggling is the same one.
    const sameSubject = deps.length === depsRef.current.length
      && deps.every((d, i) => Object.is(d, depsRef.current[i]))
    if (!sameSubject) {
      depsRef.current = deps
      failureCountRef.current = 0
      retryAtRef.current = 0
    }
    let cancelled = false
    let inFlight = false
    const isCurrent = () => !cancelled && generationRef.current === generation

    // The floor cannot exceed the cap: a caller that sets `backoffBase` above
    // `maxBackoffDelay` still backs off no further than the cap.
    const backoffFloor = Math.min(backoffBase, maxBackoffDelay)

    const scheduleNext = (delay) => {
      if (!isCurrent()) return
      if (timeoutIdRef.current) clearTimeout(timeoutIdRef.current)
      timeoutIdRef.current = setTimeout(() => {
        timeoutIdRef.current = null
        if (isCurrent()) poll()
      }, delay)
    }

    // The next retry delay for the current failure count, floored at the base
    // so the jitter's negative half cannot retry a fast poller sooner than its
    // healthy cadence.
    const nextBackoffDelay = () => Math.max(
      calculateBackoffDelay(failureCountRef.current, backoffBase, maxBackoffDelay),
      backoffFloor,
    )

    // If a backoff retry is still pending, wait only the remaining delay;
    // otherwise poll now. Used both when the effect starts (mount or a resume)
    // and when a hidden tab is shown again.
    const resumeOrPoll = () => {
      if (failureCountRef.current > 0 && retryAtRef.current) {
        scheduleNext(Math.max(0, retryAtRef.current - Date.now()))
      } else {
        poll()
      }
    }

    const poll = async () => {
      if (!isCurrent() || !enabled) return
      // A hidden tab gains nothing from the response: skip this pass and the
      // reschedule; the visibility listener below fires one poll when the tab
      // is shown again. The in-flight guard keeps a fetch that was already
      // running from being started twice when the tab becomes visible
      // mid-request.
      if (pauseWhenHidden && typeof document !== 'undefined' && document.hidden) return
      if (inFlight) return
      inFlight = true
      try {
        await fetchFnRef.current()
        // The generation can be superseded while the fetch is in the air (a
        // dep changed); its result is stale and must not schedule the new one.
        if (!isCurrent()) return
        failureCountRef.current = 0
        retryAtRef.current = 0
        scheduleNext(normalInterval)
      } catch {
        if (!isCurrent()) return
        failureCountRef.current += 1
        // One draw: retryAt and the armed timer must agree on the jitter, or a
        // resume after a hide could fire before or after what was scheduled.
        const delay = nextBackoffDelay()
        retryAtRef.current = Date.now() + delay
        scheduleNext(delay)
      } finally {
        inFlight = false
      }
    }

    const handleVisibility = () => {
      if (typeof document === 'undefined' || !isCurrent()) return
      if (document.hidden) {
        if (timeoutIdRef.current) {
          clearTimeout(timeoutIdRef.current)
          timeoutIdRef.current = null
        }
      } else if (enabled) {
        resumeOrPoll()
      }
    }

    if (enabled) {
      resumeOrPoll()
    }
    if (pauseWhenHidden) {
      document.addEventListener('visibilitychange', handleVisibility)
    }

    return () => {
      cancelled = true
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current)
        timeoutIdRef.current = null
      }
      if (pauseWhenHidden) {
        document.removeEventListener('visibilitychange', handleVisibility)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, normalInterval, maxBackoffDelay, backoffBase, pauseWhenHidden, ...deps])

  return { resetBackoff }
}
