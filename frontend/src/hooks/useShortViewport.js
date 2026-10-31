import { useEffect, useLayoutEffect, useState } from 'react'

/**
 * Window height at or under which the composer yields room to the transcript
 * (issue #1135). Kept in sync with the `html.atlas-short-viewport` rules in
 * `index.css` -- the class is the bridge, because the feedback button lives
 * outside the chat column and cannot see this hook.
 */
export const SHORT_VIEWPORT_QUERY = '(max-height: 950px)'

const SHORT_VIEWPORT_CLASS = 'atlas-short-viewport'

function viewportIsShort() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia(SHORT_VIEWPORT_QUERY).matches
  } catch {
    // jsdom and some locked-down browsers throw instead of returning a query.
    return false
  }
}

/**
 * True on short windows (a laptop, or a browser that is not maximized).
 *
 * Sets `atlas-short-viewport` on `<html>` so the composer height cap and the
 * feedback-button dock in `index.css` apply together. Call this from one
 * always-mounted component (the chat column): a second caller would remove
 * the class when it unmounts.
 */
export function useShortViewport() {
  const [matches, setMatches] = useState(viewportIsShort)

  useLayoutEffect(() => {
    const root = document.documentElement
    root.classList.toggle(SHORT_VIEWPORT_CLASS, matches)
    return () => root.classList.remove(SHORT_VIEWPORT_CLASS)
  }, [matches])

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined
    let query
    try {
      query = window.matchMedia(SHORT_VIEWPORT_QUERY)
    } catch {
      return undefined
    }
    const onChange = () => setMatches(query.matches)
    onChange()
    query.addEventListener?.('change', onChange)
    return () => query.removeEventListener?.('change', onChange)
  }, [])

  return matches
}

export default useShortViewport
