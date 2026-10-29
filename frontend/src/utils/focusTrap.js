import { useEffect, useRef } from 'react'

/**
 * Elements a focus trap may move focus through.
 *
 * Shared by every overlay trap (SettingsPanel's dialog stack, the RAG
 * drawer, the elicitation prompt) so the copies cannot drift: a selector
 * that forgets, say, `[href]` links would silently drop them from the
 * trap's rotation in one overlay only. Disabled form fields are excluded
 * alongside disabled buttons, or a trap could swallow a Tab and then focus
 * nothing at all.
 */
export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Focusable elements inside `container` that are actually rendered.
 *
 * getClientRects() rather than offsetParent for the visibility filter:
 * offsetParent is null inside a `position: fixed` ancestor, which is
 * exactly how the nested modals in the Tools and Settings panel render.
 */
export const getFocusableElements = (container) =>
  Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR))
    .filter(el => el.getClientRects().length > 0)

/**
 * Whether `container`'s overlay group owns the keyboard right now.
 *
 * Ownership among SIBLING overlays is positional: the LAST
 * `[role="dialog"][aria-modal="true"]` in document order wins. Every
 * aria-modal overlay this app renders shares the same stacking layer
 * (Tailwind's z-50), where equal z-index stacks in DOM order -- App renders
 * the drawer, then the Tools and Settings panel, then the elicitation
 * prompt, and the prompt/confirm dialog host renders after all of them.
 * Positional ownership is therefore the stacking order, and it needs no
 * registration machinery to stay correct.
 *
 * A dialog layered INSIDE another overlay -- the unsaved-changes prompt,
 * the token input, the admin config editor, the disconnect confirmation --
 * is not a competitor: it belongs to that overlay's group. The parent keeps
 * ownership while its own nested dialog is the topmost modal, so its trap
 * can scope Tab to the innermost open dialog and hand Escape off to the
 * nested overlay's own handler (#1039). Without the containment rule the
 * parent would stand down entirely and Tab would simply escape the nested
 * dialog into the overlay behind it.
 *
 * Deliberately not a focus-position check: clicking non-focusable text in a
 * modal drops focus to <body>, and a focus-based check would hand keys back
 * to the overlay underneath.
 */
export const isTopmostModalDialog = (container) => {
  if (!container) return false
  const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]')
  if (modals.length === 0) return false
  const topmost = modals[modals.length - 1]
  return topmost === container || container.contains(topmost)
}

/**
 * The dialog a trap should scope to: the last RENDERED `[role="dialog"]`
 * inside `container`, or `container` itself when none is open.
 *
 * Nested dialogs own their slice of the overlay while they are up: Tab is
 * trapped inside the innermost one, so focus cannot wander behind a prompt
 * the user still has to answer, and Escape stands down entirely (the nested
 * dialog's own close-on-Escape handles the key).
 *
 * Filtered on getClientRects() like getFocusableElements: a nested dialog
 * that is closed but still mounted must never hijack the scope, or the trap
 * would rotate through elements that are not on screen. If none is rendered
 * the overlay itself is the scope.
 */
export const getInnermostDialog = (container) => {
  if (!container) return null
  const nested = Array.from(container.querySelectorAll('[role="dialog"]'))
    .filter(el => el.getClientRects().length > 0)
  return nested.length > 0 ? nested[nested.length - 1] : container
}

/**
 * Focus management for a modal overlay: focus enters when it activates, Tab
 * is trapped inside while it is up, Escape is delegated to `onEscape`, and
 * focus returns to where it was when it deactivates. This one hook serves
 * every outermost overlay -- the Tools and Settings panel (with its nested
 * dialogs), the Data Sources drawer, the elicitation prompt -- so the
 * trap/Escape behavior cannot drift between them again (#1039).
 *
 * - `initialFocusSelector` picks the entry focus target within the overlay
 *   (the first matching, rendered element), falling back to the first
 *   focusable. The elicitation prompt uses it to land on the first form
 *   field -- or Accept, when there are no fields -- instead of its Cancel
 *   button: the prompt often mounts while the user is typing, and focus
 *   landing on Cancel would make the next Space or Enter cancel the tool's
 *   request.
 * - `onEscape` closes the overlay. The listener sits in the BUBBLE phase on
 *   purpose: nested overlays close themselves through useEscapeKey, which
 *   listens in the CAPTURE phase and stops propagation, so a nested prompt
 *   always gets the key first and reaching this hook's handler means no
 *   nested overlay wanted it. Escape also stands down unless the overlay is
 *   the topmost modal group (a prompt layered on top keeps the key) and
 *   unless the event was aimed at this overlay or at one of the "focus fell
 *   out entirely" targets (<body>, <html>, `document`) -- a keydown aimed
 *   at an element outside the overlay must not close it.
 * - The listener lives on `document`, not the container: focus can
 *   legitimately sit outside while the overlay is up (e.g. <body> after a
 *   control disabled itself), and a keydown on the container never sees it.
 * - Tab is trapped inside the innermost open dialog rather than switched
 *   off: standing down would let focus walk out of the overlay entirely.
 * - At most one listener in the app ever acts on a given Escape: every
 *   overlay (this hook and useEscapeKey alike) checks ownership BEFORE
 *   stopping propagation, so listener registration order never decides who
 *   handles the key -- the topmost-dialog check does. That is also why
 *   stopPropagation (not stopImmediatePropagation) is enough here: sibling
 *   overlays stand down on their own, and stopping propagation only needs
 *   to keep window-level listeners (the prompt/confirm dialog host) from
 *   also reacting.
 * - Focus restore is skipped when the overlay deactivates beneath a newer
 *   modal: restoring would yank focus behind the newer backdrop.
 * - The previous-focus element is cleared on deactivation either way; it is
 *   stale once the overlay is gone.
 */
export function useFocusTrap({ containerRef, active, initialFocusSelector, onEscape }) {
  const previousFocusRef = useRef(null)
  const onEscapeRef = useRef(onEscape)

  useEffect(() => {
    onEscapeRef.current = onEscape
  }, [onEscape])

  useEffect(() => {
    if (!active) return undefined
    const container = containerRef.current
    if (!container) return undefined

    previousFocusRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null

    const initialBySelector = initialFocusSelector
      ? Array.from(container.querySelectorAll(initialFocusSelector))
        .find(el => el.getClientRects().length > 0)
      : null
    ;(initialBySelector || getFocusableElements(container)[0])?.focus()

    // Escape aimed somewhere that is not this overlay must not close it. The
    // listener lives on the document, so scope by target: the overlay itself
    // counts, and so does a bare <body>/<html>/`document` target, which is
    // where focus lands after clicking non-focusable text (or where a
    // synthetic keydown without a focused element is dispatched).
    const escapeIsOurs = (event) => {
      if (!(event.target instanceof Node)) return true
      return container.contains(event.target)
        || event.target === document
        || event.target === document.body
        || event.target === document.documentElement
    }

    const onKeyDown = (event) => {
      if (event.key !== 'Escape' && event.key !== 'Tab') return
      // A modal layered on top of this overlay -- the elicitation prompt
      // over the panel, the panel over the drawer -- owns both keys while it
      // is up. Without this, Escape with focus on <body> (clicking
      // non-focusable text drops it there) closed the overlay underneath.
      if (!isTopmostModalDialog(container)) return
      const scope = getInnermostDialog(container)

      if (event.key === 'Escape') {
        // A nested overlay owns its own Escape (see useEscapeKey, which
        // listens in the capture phase and stops propagation), so reaching
        // here with one open means the key is not ours.
        if (scope !== container) return
        if (!escapeIsOurs(event)) return
        event.stopPropagation()
        if (typeof onEscapeRef.current === 'function') onEscapeRef.current()
        return
      }

      const focusable = getFocusableElements(scope)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const activeEl = document.activeElement
      // Focus is outside the trapped scope (the body, after a click on plain
      // text): pull it back in rather than letting Tab walk the page behind.
      if (!scope.contains(activeEl)) {
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

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
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
      // Nested dialogs of our own do not count as "another modal": they
      // detach with us, and restoring past them is correct.
      const anotherModalOpen = container.isConnected
        && [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')]
          .some(m => m !== container && !container.contains(m))
      if (anotherModalOpen) return
      if (previous instanceof HTMLElement) previous.focus()
    }
  // Keyed on `active` alone: `onEscape` changes identity per render (an
  // inline arrow, a requestClose that goes dirty with staged edits) and is
  // read through the latest-ref above, so re-running this effect -- and
  // yanking focus back to the initial target mid-edit -- never happens.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active])
}