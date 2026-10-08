import { useEffect, useRef } from 'react'

/**
 * Elements a focus trap may move focus through.
 *
 * Shared by every overlay trap (SettingsPanel's dialog stack, the RAG
 * drawer, the elicitation prompt) so the copies cannot drift: a selector
 * that forgets, say, `[href]` links would silently drop them from the
 * trap's rotation in one overlay only.
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'

/**
 * Whether `container` is the topmost open modal dialog.
 *
 * Overlay ownership goes to the LAST `[role="dialog"][aria-modal="true"]` in
 * document order: at equal z-index, later siblings stack above earlier ones.
 * This relies on App rendering later overlays (SettingsPanel, then the
 * elicitation prompt) after earlier ones -- the useFocusTrap refactor in
 * #1039 should make ownership explicit instead of positional. Deliberately
 * not a focus-position check: clicking non-focusable text in a modal drops
 * focus to <body>, and a focus-based check would hand keys back to the
 * overlay underneath.
 */
export const isTopmostModalDialog = (container) => {
  if (!container) return false
  const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]')
  return modals.length > 0 && modals[modals.length - 1] === container
}

/**
 * Focus management for a modal overlay: focus enters when it activates, Tab
 * is trapped inside while it is up, and focus returns to where it was when
 * it deactivates.
 *
 * - The listener lives on `document`, not the container: focus can
 *   legitimately sit outside while the overlay is up (e.g. <body> after a
 *   control disabled itself), and a keydown on the container never sees it.
 * - The trap stands down unless the overlay is the topmost modal dialog, so
 *   an overlay layered on top keeps the keys.
 * - Focus restore is skipped when the overlay deactivates beneath a newer
 *   modal: restoring would yank focus behind the newer backdrop.
 * - The previous-focus element is cleared on deactivation either way; it is
 *   stale once the overlay is gone.
 */
export function useFocusTrap({ containerRef, active }) {
  const previousFocusRef = useRef(null)

  useEffect(() => {
    if (!active) return undefined
    const container = containerRef.current
    if (!container) return undefined

    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusable = container.querySelectorAll(FOCUSABLE_SELECTOR)
    focusable[0]?.focus()

    const trapTab = (event) => {
      if (event.key !== 'Tab') return
      if (!isTopmostModalDialog(container)) return
      const focusable = container.querySelectorAll(FOCUSABLE_SELECTOR)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const activeEl = document.activeElement
      if (!container.contains(activeEl)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (event.shiftKey && activeEl === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && activeEl === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', trapTab)
    return () => {
      document.removeEventListener('keydown', trapTab)
      const previous = previousFocusRef.current
      previousFocusRef.current = null
      // Restore only if no OTHER modal was layered on top while this one was
      // up. Two shapes of deactivation exist:
      //   - the overlay is conditionally rendered and now detached from the
      //     document (the elicitation prompt): the modals that remain are
      //     OLDER ones it sat above, and focus hands back to them;
      //   - the overlay is always mounted and merely closed (the drawer):
      //     any remaining aria-modal dialog is a newer one on top, and
      //     restoring would yank focus behind its backdrop.
      const anotherModalOpen = container.isConnected
        && [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
          .some(m => m !== container)
      if (anotherModalOpen) return
      if (previous instanceof HTMLElement) previous.focus()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])
}
