import { X } from 'lucide-react'
import DataSourcesSelector from './DataSourcesSelector'

/**
 * Left-hand Data Sources drawer.
 *
 * The picker itself lives in DataSourcesSelector so it can be reused by the
 * Data Sources tab in the Tools and Settings panel (issue #839 review); this
 * component is only the drawer chrome.
 *
 * The drawer is an overlay at every breakpoint: it stays `fixed` and out of
 * document flow so opening it never reflows the chat layout (issue #1037).
 * A full-screen backdrop (also at desktop widths) closes it on outside click.
 */
const RagPanel = ({ isOpen, onClose }) => {
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
            onClick={onClose}
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
