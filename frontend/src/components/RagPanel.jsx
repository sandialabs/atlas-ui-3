import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import DataSourcesSelector from './DataSourcesSelector'
import { useEscapeKey } from '../hooks/useEscapeKey'

/**
 * Left-hand Data Sources drawer.
 *
 * The picker itself lives in DataSourcesSelector so it can be reused by the
 * Data Sources tab in the Tools and Settings panel (issue #839 review); this
 * component is only the drawer chrome.
 *
 * The drawer is an overlay at every breakpoint: it stays `fixed` and out of
 * document flow so opening it never reflows the chat layout (issue #1037).
 * A full-screen backdrop (also at desktop widths) closes it on outside click
 * or Escape. The drawer covers the banner strip while open, the same way it
 * always has at mobile widths.
 */
const RagPanel = ({ isOpen, onClose }) => {
  // The header toggle (Header.jsx renders the button with this id) regains
  // focus when the drawer closes so keyboard users keep their place.
  const TOGGLE_ID = 'rag-drawer-toggle'
  const closeButtonRef = useRef(null)

  useEscapeKey(isOpen, onClose)

  // Focus lands in the drawer when it opens, and returns to the header toggle
  // when it closes -- otherwise closing via Escape or the backdrop leaves the
  // focus on <body> and keyboard users lose their place.
  useEffect(() => {
    if (isOpen) closeButtonRef.current?.focus()
  }, [isOpen])

  const wasOpenRef = useRef(isOpen)
  useEffect(() => {
    if (wasOpenRef.current && !isOpen) {
      document.getElementById(TOGGLE_ID)?.focus()
    }
    wasOpenRef.current = isOpen
  }, [isOpen])

  return (
    <>
      {/* Overlay */}
      {isOpen && (
        <div
          data-testid="rag-drawer-backdrop"
          className="fixed inset-0 bg-black bg-opacity-50 z-40"
          onClick={onClose}
        />
      )}

      {/* Panel */}
      <aside data-testid="rag-drawer" aria-hidden={!isOpen} inert={!isOpen} className={`
        fixed left-0 top-0 h-full w-80 lg:w-96 bg-gray-800 border-r border-gray-700 z-50 transform transition-transform duration-300 ease-in-out flex flex-col
        ${isOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-lg font-semibold text-gray-100">Data Sources</h2>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="Close data sources drawer"
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <DataSourcesSelector />
      </aside>
    </>
  )
}

export default RagPanel
