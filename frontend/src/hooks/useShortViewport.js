import { useEffect, useState } from 'react'

/**
 * Window height at or under which the composer yields room to the transcript
 * (issue #1135). Kept in sync with the `@media (max-height: 950px)` rules in
 * `index.css`: the hook drives the JS-side conditionals (the attached-files
 * collapse, the compact status row, the hidden workspace switcher) and the
 * media query drives the styling, so a wrong class on <html> could never
 * desynchronize the two.
 */
export const SHORT_VIEWPORT_QUERY = '(max-height: 950px)'

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
 * Drive only component state with this. The matching CSS lives in a media
 * query in `index.css`, not behind a class this hook would have to toggle --
 * a single unmounting caller would otherwise switch the short layout off
 * for the whole page.
 */
export function useShortViewport() {
  const [matches, setMatches] = useState(viewportIsShort)

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
