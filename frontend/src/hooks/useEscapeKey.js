import { useEffect } from 'react'

/**
 * Close-on-Escape for a modal overlay.
 *
 * Each overlay handles its own Escape rather than leaning on an ancestor: the
 * combined "Tools and Settings" panel deliberately stands down while a nested
 * dialog is open (issue #839 review), so without this Escape is a no-op on the
 * unsaved-changes prompt, the token input, and the admin config editor.
 *
 * Listens in the capture phase and stops propagation, so the innermost open
 * overlay wins and the panel behind it does not also close.
 */
export function useEscapeKey(isOpen, onEscape) {
  useEffect(() => {
    if (!isOpen || typeof onEscape !== 'function') return undefined
    const onKeyDown = (event) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      onEscape()
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [isOpen, onEscape])
}
