/**
 * Tracks the rendered width of a single element.
 *
 * Exists because the header's responsive breakpoints have to react to the space
 * the header actually gets, not to the viewport. The header is laid out inside
 * `flex-1` next to a 256px sidebar, so a viewport media query such as
 * `min-[1280px]:flex` turns on while the header itself is only ~1024px wide --
 * revealing the desktop button cluster roughly a sidebar-width too early, which
 * pushed the trailing buttons past the header's right edge and let the model
 * selector paint over the save-mode button.
 *
 * A CSS container query would be the natural fix, but `container-type:
 * inline-size` applies layout containment, which makes the element a containing
 * block for its `position: fixed` descendants. The header contains three of
 * them (the mobile-menu backdrop, the menu panel, and the API key modal), so
 * containing the header would break all three. Measuring in JS keeps those
 * positioned against the viewport, as they were.
 *
 * The first measurement is taken in useLayoutEffect, before paint, so a wide
 * header never flashes its compact layout on mount. ResizeObserver then keeps
 * it current; it is absent in jsdom-style environments, where the one-shot
 * layout measurement is still correct for a static test viewport.
 *
 * Returns `[ref, width]`. `width` is 0 until the element is attached, so
 * callers should treat 0 as "not measured yet" -- for a min-width threshold the
 * compact branch is the safe default, which is what 0 naturally selects.
 */

import { useCallback, useLayoutEffect, useRef, useState } from 'react'

export function useElementWidth() {
  const ref = useRef(null)
  const [width, setWidth] = useState(0)

  // Only re-render when the integer width actually changes. ResizeObserver
  // reports sub-pixel fractions that would otherwise churn renders on any
  // reflow that leaves the layout visually identical.
  const measure = useCallback((element) => {
    const next = Math.round(element.getBoundingClientRect().width)
    setWidth((prev) => (prev === next ? prev : next))
  }, [])

  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return

    measure(element)

    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => measure(element))
    observer.observe(element)
    return () => observer.disconnect()
  }, [measure])

  return [ref, width]
}

export default useElementWidth
