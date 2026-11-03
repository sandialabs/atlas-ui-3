import { useEffect } from 'react'

/**
 * Publishes the top edge of the chat area (in viewport pixels) as the CSS
 * custom property `--atlas-chat-top` on <html>.
 *
 * Everything above the chat area is chrome of varying height: the admin
 * banner messages (BannerPanel, which can wrap), the compliance classification
 * banner, and the header. Panels that open upward from the composer on a phone
 * cap their height to the room between the toolbar and this edge; a constant
 * guess at the chrome height let a panel's top, and the tools search field
 * with it, slide under the header once a banner message was showing.
 *
 * The chat area fills the app shell under that chrome, so a change in the
 * chrome resizes it and the ResizeObserver republishes. Window and visual
 * viewport resizes (rotation, on-screen keyboard) are covered as well.
 *
 * Returns nothing; consumers read the variable from CSS.
 */
export const CHAT_TOP_VAR = '--atlas-chat-top'

export function useChatTopVar(ref) {
  useEffect(() => {
    const el = ref?.current
    const root = typeof document !== 'undefined' ? document.documentElement : null
    if (!el || !root) return

    const publish = () => {
      const top = el.getBoundingClientRect?.().top ?? 0
      root.style.setProperty(CHAT_TOP_VAR, `${Math.max(0, Math.round(top))}px`)
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
