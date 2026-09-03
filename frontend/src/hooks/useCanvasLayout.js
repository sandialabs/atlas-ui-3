import { useCallback, useEffect, useState } from 'react'
import { usePersistentState } from './chat/usePersistentState'

export const CANVAS_SIZES = ['half', 'full']
export const CANVAS_ORIENTATIONS = ['right', 'top']

const SIZE_KEY = 'chatui-canvas-size'
const ORIENTATION_KEY = 'chatui-canvas-orientation'

// Below this width a side-by-side split leaves both panes too narrow to read,
// so the canvas stacks above the chat no matter what the stored preference says.
export const NARROW_VIEWPORT_PX = 768

function readIsNarrow() {
  if (typeof window === 'undefined') return false
  return window.innerWidth < NARROW_VIEWPORT_PX
}

/**
 * Canvas layout preference: how much room the canvas takes ("half" or "full")
 * and where it sits relative to the chat ("right" or "top"). Persisted per
 * browser so the same device keeps the user's choice across sessions.
 */
export function useCanvasLayout() {
  const [storedSize, setSize] = usePersistentState(SIZE_KEY, 'half')
  const [storedOrientation, setOrientation] = usePersistentState(ORIENTATION_KEY, 'right')
  const [isNarrow, setIsNarrow] = useState(readIsNarrow)

  useEffect(() => {
    const onResize = () => setIsNarrow(readIsNarrow())
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const size = CANVAS_SIZES.includes(storedSize) ? storedSize : 'half'
  const orientation = CANVAS_ORIENTATIONS.includes(storedOrientation) ? storedOrientation : 'right'

  // What the layout actually renders as. The stored preference is left alone so
  // rotating back to a wide viewport restores the user's side-by-side choice.
  const effectiveOrientation = isNarrow ? 'top' : orientation

  const toggleSize = useCallback(() => {
    setSize((prev) => (prev === 'full' ? 'half' : 'full'))
  }, [setSize])

  const toggleOrientation = useCallback(() => {
    setOrientation((prev) => (prev === 'top' ? 'right' : 'top'))
  }, [setOrientation])

  return { size, orientation, effectiveOrientation, isNarrow, toggleSize, toggleOrientation }
}

export default useCanvasLayout
