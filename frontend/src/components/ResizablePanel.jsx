import { useState, useCallback, useEffect, useRef } from 'react'

const ResizablePanel = ({ 
  children, 
  isOpen, 
  onClose, 
  defaultWidth = 448, // 28rem = 448px
  minWidth = 320,     // 20rem = 320px
  maxWidth = 800,     // 50rem = 800px
  className = "",
  onWidthChange
}) => {
  const [width, setWidth] = useState(() => Math.min(defaultWidth, window.innerWidth))
  const [isResizing, setIsResizing] = useState(false)
  const resizeRef = useRef(null)
  const panelRef = useRef(null)

  const startResize = useCallback((e) => {
    setIsResizing(true)
    e.preventDefault()
  }, [])

  const stopResize = useCallback(() => {
    setIsResizing(false)
  }, [])

  const resize = useCallback((e) => {
    if (isResizing && panelRef.current) {
      const rect = panelRef.current.getBoundingClientRect()
      const newWidth = rect.right - e.clientX
      const clampedWidth = Math.min(Math.max(newWidth, minWidth), maxWidth)
      setWidth(clampedWidth)
      onWidthChange?.(clampedWidth)
    }
  }, [isResizing, minWidth, maxWidth, onWidthChange])

  useEffect(() => {
    const handleMouseMove = (e) => resize(e)
    const handleMouseUp = () => stopResize()

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove)
      document.addEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    } else {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isResizing, resize, stopResize])

  // Adjust panel width when window is resized to ensure it fits within viewport
  useEffect(() => {
    const handleWindowResize = () => {
      if (window.innerWidth < width) {
        const clampedWidth = Math.min(Math.max(window.innerWidth, minWidth), maxWidth)
        setWidth(clampedWidth)
        onWidthChange?.(clampedWidth)
      }
    }
    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [width, minWidth, maxWidth, onWidthChange])

  return (
    <>
      {/* Overlay */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black bg-opacity-50 z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      {/* Panel */}
      <aside
        ref={panelRef}
        className={`
          absolute right-0 top-0 h-full bg-gray-800 border-l border-gray-700 z-50 transform transition-transform duration-300 ease-in-out flex flex-col
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}
          ${className}
        `}
        style={{
          width: isOpen ? `${width}px` : '0px',
          minWidth: isOpen ? `${minWidth}px` : '0px',
          maxWidth: `${maxWidth}px`,
          display: isOpen ? 'flex' : 'none',
        }}
      >
        {/* Resize Handle */}
        {isOpen && (
          <div
            ref={resizeRef}
            className="absolute left-0 top-0 w-1 h-full cursor-col-resize bg-transparent hover:bg-blue-500/50 transition-colors group block"
            onMouseDown={startResize}
          >
            <div className="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-12 bg-gray-600 group-hover:bg-blue-500 transition-colors rounded-r-sm" />
          </div>
        )}

        {children}
      </aside>
    </>
  )
}

export default ResizablePanel
