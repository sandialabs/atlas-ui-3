import { useCallback, useRef } from 'react'
import { copyCodeBlock } from '../utils/clipboard'

// Wires up the Copy buttons that markdownRenderer.js emits inside rendered
// code blocks. The renderer only produces markup (`data-action="copy-code"`),
// so every surface that renders shared markdown -- chat messages, the canvas,
// the help page, the splash screen -- needs a delegated click listener on its
// container, or its Copy buttons are inert (#1151).
//
// Returns a callback ref rather than taking a ref object, so the listener
// attaches whenever the container mounts, including containers that render
// conditionally after the component's first render (the splash screen
// returns null until it is visible).
export const useCodeBlockCopy = () => {
  const detachRef = useRef(null)

  return useCallback((node) => {
    if (detachRef.current) {
      detachRef.current()
      detachRef.current = null
    }
    if (!node) return

    const handleClick = (event) => {
      const button = event.target.closest?.('[data-action="copy-code"]')
      if (!button || !node.contains(button)) return
      event.preventDefault()
      copyCodeBlock(button)
    }
    node.addEventListener('click', handleClick)
    detachRef.current = () => node.removeEventListener('click', handleClick)
  }, [])
}
