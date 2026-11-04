import { useEffect } from 'react'

/**
 * Publishes the top edge of the chat area, in document pixels, as the CSS
 * custom property `--atlas-chat-top` on <html>.
 *
 * Everything above the chat area is chrome of varying height: the admin
 * banner messages (BannerPanel, which can wrap), the compliance classification
 * banner, and the header. Panels that open upward from the composer on a phone
 * cap their height to the room between the toolbar and this edge; a constant
 * guess at the chrome height let a panel's top, and the tools search field
 * with it, slide under the header once a banner message was showing.
 *
 * The chat area's top is also the panels' clipping edge: its root is
 * overflow-hidden, so a panel can never paint above it. That is why the edge
 * is the chat area itself and not just the header and banners -- on a phone an
 * open canvas stacks between the header and the chat area, and a panel capped
 * to the header alone ran under the canvas and lost its top (search field
 * included) to the clip.
 *
 * The value is measured against the app's root element (#root), so it does
 * not move when the page or the visual viewport pans -- iOS pans the page
 * while the on-screen keyboard is up -- because both rects shift together. The
 * app shell is sized to the visual viewport (--app-viewport-height), so the
 * room for a panel is that height less this edge less the toolbar band.
 *
 * The chat area fills the app shell under the chrome, so a change in the
 * chrome resizes it and the ResizeObserver republishes; window and visual
 * viewport resizes (rotation, keyboard) are covered as well. Writes are
 * skipped when the value is unchanged.
 *
 * Returns nothing; consumers read the variable from CSS.
 */
export const CHAT_TOP_VAR = '--atlas-chat-top'

export function useChatTopVar(ref) {
  useEffect(() => {
    const el = ref?.current
    const root = typeof document !== 'undefined' ? document.documentElement : null
    if (!el || !root) return

    let last = null
    const publish = () => {
      // A hidden chat area (a full-size canvas) has no box; keep the last
      // value rather than publishing 0.
      if (!el.getClientRects?.().length) return
      const origin = document.getElementById('root') ?? root
      const top = el.getBoundingClientRect().top - origin.getBoundingClientRect().top
      const value = `${Math.round(top)}px`
      if (value === last) return
      last = value
      root.style.setProperty(CHAT_TOP_VAR, value)
    }

    publish()

    // ResizeObserver is absent in jsdom and in a few locked-down browsers; the
    // one-shot measurement above and the resize listeners still apply.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publish)
    observer?.observe(el)
    window.addEventListener('resize', publish)
    window.visualViewport?.addEventListener('resize', publish)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', publish)
      window.visualViewport?.removeEventListener('resize', publish)
      // Clear on unmount so CSS falls back to its default instead of a stale
      // offset.
      root.style.removeProperty(CHAT_TOP_VAR)
    }
  }, [ref])
}

export default useChatTopVar
