import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'
import DataSourcesSelector from './DataSourcesSelector'
import { useEscapeKey } from '../hooks/useEscapeKey'
import { FOCUSABLE_SELECTOR } from '../utils/focusTrap'

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

  // The Tools and Settings modal can be layered on top of an open drawer.
  // Ownership goes to the TOPMOST modal dialog in document order (later
  // siblings stack above earlier ones at equal z-index), decided by the
  // aria-modal attribute rather than focus position: clicking non-focusable
  // text in the modal drops focus to <body>, and a focus-based check would
  // hand Escape and Tab straight back to the drawer beneath it. The drawer
  // stands down when it is not the topmost modal; useEscapeKey consults the
  // predicate BEFORE stopping propagation, so the modal's own (bubble-phase)
  // Escape handler still receives the key.
  const isTopmostModalDialog = () => {
    if (!drawerRef.current) return false
    const modals = document.querySelectorAll('[role="dialog"][aria-modal="true"]')
    return modals.length > 0 && modals[modals.length - 1] === drawerRef.current
  }

  useEscapeKey(isOpen, onClose, { shouldHandle: isTopmostModalDialog })

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement
      closeButtonRef.current?.focus()
      return
    }
    // Focus that moved into another dialog while the drawer was up (the
    // Tools and Settings modal) belongs to that dialog; restoring here
    // would yank focus behind its backdrop. Either way the saved element
    // is stale once the drawer is gone.
    const previous = previousFocusRef.current
    previousFocusRef.current = null
    if (!isTopmostModalDialog()) return
    if (previous instanceof HTMLElement) previous.focus()
  }, [isOpen])

  // Tab is trapped inside the drawer rather than switched off: without this,
  // focus could walk the covered header controls behind the backdrop, and a
  // press on one of them could close a second overlay together with this
  // one. The listener lives on `document` (not on the <aside>) because focus
  // can legitimately sit outside the drawer while it is up -- e.g. on <body>
  // after "Clear All" disabled the control that held it -- and a keydown on
  // the <aside> itself would never see it. Stands down when the drawer is
  // not the topmost modal dialog (same predicate as Escape: the Tools and
  // Settings modal layered on top owns the trap). No visibility filter on
  // the focusables (the SettingsPanel one works around nested fixed-position
  // modals this drawer does not have; when the drawer is closed it is inert,
  // so Tab never reaches it anyway). Not a shared hook yet: SettingsPanel's
  // trap is innermost-dialog aware in ways this drawer does not need;
  // tracked in issue #1039.
  useEffect(() => {
    if (!isOpen) return undefined

    const trapTab = (event) => {
      if (event.key !== 'Tab' || !drawerRef.current) return
      // Same ownership rule as Escape: the topmost modal dialog owns Tab.
      if (!isTopmostModalDialog()) return

      const focusable = drawerRef.current.querySelectorAll(FOCUSABLE_SELECTOR)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (!drawerRef.current.contains(active)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (event.shiftKey && active === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', trapTab)
    return () => document.removeEventListener('keydown', trapTab)
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
      <aside
        ref={drawerRef}
        id="rag-drawer"
        data-testid="rag-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rag-drawer-title"
        aria-hidden={!isOpen}
        inert={!isOpen}
        className={`
          fixed left-0 top-0 h-full w-80 lg:w-96 bg-gray-800 border-r border-gray-700 z-50 transform transition-transform duration-300 ease-in-out motion-reduce:transition-none flex flex-col
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
