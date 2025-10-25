import { X } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const RagPanel = ({ isOpen, onClose }) => {
  const { 
    dataSources, 
    selectedDataSources, 
    toggleDataSource, 
    onlyRag, 
    setOnlyRag 
  } = useChat()

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
      <aside className={`
        fixed left-0 top-0 h-full w-80 bg-gray-800 border-r border-gray-700 z-50 transform transition-transform duration-300 ease-in-out
        ${isOpen ? 'translate-x-0' : '-translate-x-full'}
        lg:relative lg:translate-x-0 lg:w-96
        ${!isOpen ? 'lg:hidden' : ''}
      `}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-gray-100">Data Sources</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* RAG Controls */}
        <div className="p-4 border-b border-gray-700">
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={onlyRag}
              onChange={(e) => setOnlyRag(e.target.checked)}
              className="w-4 h-4 text-blue-600 bg-gray-700 border-gray-600 rounded focus:ring-blue-500 focus:ring-2"
            />
            <span className="text-sm text-gray-200 font-medium">Only RAG</span>
          </label>
        </div>

        {/* Data Sources List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-4">
          {dataSources.length === 0 ? (
            <div className="text-gray-400 text-center py-8">No data sources available</div>
          ) : (
            <div className="space-y-3">
              {dataSources.map(dataSource => {
                const isSelected = selectedDataSources.has(dataSource)
                
                return (
                  <div
                    key={dataSource}
                    onClick={() => toggleDataSource(dataSource)}
                    className={`p-4 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-blue-600 border-blue-500 text-white'
                        : 'bg-gray-700 border-gray-600 text-gray-200 hover:bg-gray-600'
                    }`}
                  >
                    <div className="font-medium">
                      {dataSource.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    </div>
                    <div className="text-sm mt-1 opacity-80">
                      Click to {isSelected ? 'deselect' : 'select'} this data source
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </aside>
    </>
  )
}

export default RagPanel