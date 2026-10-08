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
 * or Escape, and it behaves as a modal: focus enters on open, Tab stays
 * inside while it is up, and focus returns to where it was on close. The
 * drawer covers the banner strip while open, the same way it always has at
 * mobile widths.
 */
const RagPanel = ({ isOpen, onClose }) => {
  const closeButtonRef = useRef(null)
  const drawerRef = useRef(null)
  // Focus held when the drawer opened (the header toggle on a keyboard
  // open): restored on close so keyboard users keep their place.
  const previousFocusRef = useRef(null)

  useEscapeKey(isOpen, onClose)

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement
      closeButtonRef.current?.focus()
      return
    }
    if (previousFocusRef.current instanceof HTMLElement) {
      previousFocusRef.current.focus()
      previousFocusRef.current = null
    }
  }, [isOpen])

  // Tab is trapped inside the drawer rather than switched off: without this,
  // focus could walk the covered header controls behind the backdrop, and a
  // press on one of them could close a second overlay together with this
  // one. No visibility filter on the focusables (the SettingsPanel one works
  // around nested fixed-position modals this drawer does not have; when the
  // drawer is closed it is inert, so Tab never reaches it anyway).
  const trapTab = (event) => {
    if (event.key !== 'Tab' || !drawerRef.current) return
    const focusable = drawerRef.current.querySelectorAll(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
    )
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (!drawerRef.current.contains(document.activeElement)) {
      event.preventDefault()
      ;(event.shiftKey ? last : first).focus()
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

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
      <aside
        ref={drawerRef}
        data-testid="rag-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rag-drawer-title"
        aria-hidden={!isOpen}
        inert={!isOpen}
        onKeyDown={trapTab}
        className={`
          fixed left-0 top-0 h-full w-80 lg:w-96 bg-gray-800 border-r border-gray-700 z-50 transform transition-transform duration-300 ease-in-out flex flex-col
          ${isOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700 flex-shrink-0">
          <h2 id="rag-drawer-title" className="text-lg font-semibold text-gray-100">Data Sources</h2>
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
