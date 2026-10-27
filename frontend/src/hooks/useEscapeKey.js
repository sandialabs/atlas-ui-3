import { useEffect, useRef } from 'react'

/**
 * Close-on-Escape for an INNER overlay.
 *
 * Each overlay handles its own Escape rather than leaning on an ancestor: the
 * combined "Tools and Settings" panel deliberately stands down while a nested
 * dialog is open (issue #839 review), so without this Escape is a no-op on the
 * unsaved-changes prompt, the token input, and the admin config editor. These
 * are all nested or dropdown surfaces -- the OUTERMOST aria-modal overlays
 * (Data Sources drawer, Tools and Settings panel, elicitation prompt) have
 * delegated Escape to the shared useFocusTrap since #1039, which listens in
 * the BUBBLE phase; this hook's capture-phase listeners therefore always see
 * the key first, and a nested prompt reliably beats the panel behind it.
 *
 * Listens in the capture phase and stops propagation, so the innermost open
 * overlay wins and the overlay behind it does not also close. A
 * `shouldHandle` predicate lets an overlay decline the key entirely BEFORE
 * propagation is stopped -- an overlay beneath another modal stands down
 * while focus is in that modal, and stopping propagation first would swallow
 * the modal's Escape before its own (bubble-phase) handler ever saw the key.
 *
 * On stopImmediatePropagation (#1039): it is deliberately NOT used. Every
 * overlay in the app -- this hook and useFocusTrap alike -- checks ownership
 * BEFORE stopping propagation, so at most one overlay ever acts on a given
 * Escape and listener registration order never decides who handles it; the
 * topmost-dialog check does. Plain stopPropagation is exactly enough: it
 * holds the key back from window-level listeners (the prompt/confirm dialog
 * host) without suppressing the remaining same-node listeners, which have
 * already stood down on their own.
 *
 * `onEscape` and `shouldHandle` are held in latest-refs and the listener is
 * keyed on `isOpen` alone: handlers that change identity per render (an
 * inline arrow, a `requestClose` that goes dirty with staged edits) would
 * otherwise remove and re-add the listener on every render while open.
 */
export function useEscapeKey(isOpen, onEscape, { shouldHandle } = {}) {
  const onEscapeRef = useRef(onEscape)
  const shouldHandleRef = useRef(shouldHandle)

  useEffect(() => {
    onEscapeRef.current = onEscape
  }, [onEscape])
  useEffect(() => {
    shouldHandleRef.current = shouldHandle
  }, [shouldHandle])

  useEffect(() => {
    if (!isOpen) return undefined
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return
      if (typeof onEscapeRef.current !== 'function') return
      const predicate = shouldHandleRef.current
      if (typeof predicate === 'function' && !predicate()) return
      event.stopPropagation()
      onEscapeRef.current()
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [isOpen])
}