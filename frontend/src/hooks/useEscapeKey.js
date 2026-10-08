import { useEffect, useRef } from 'react'

/**
 * Close-on-Escape for a modal overlay.
 *
 * Each overlay handles its own Escape rather than leaning on an ancestor: the
 * combined "Tools and Settings" panel deliberately stands down while a nested
 * dialog is open (issue #839 review), so without this Escape is a no-op on the
 * unsaved-changes prompt, the token input, and the admin config editor.
 *
 * Listens in the capture phase and stops propagation, so the innermost open
 * overlay wins and the panel behind it does not also close. A `shouldHandle`
 * predicate lets an overlay decline the key entirely BEFORE propagation is
 * stopped -- a drawer beneath a modal stands down while focus is in that
 * modal, and stopping propagation first would swallow the modal's Escape
 * before its own (bubble-phase) handler ever saw the key.
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
