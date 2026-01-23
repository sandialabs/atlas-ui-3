import { useMemo, useState } from 'react'
import { X, Search } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const RagPanel = ({ isOpen, onClose }) => {
  const {
    ragSources,
    selectedDataSources,
    toggleDataSource,
    features,
    complianceLevelFilter
  } = useChat()

  const [searchQuery, setSearchQuery] = useState('')

  const complianceLevelsEnabled = features.compliance_levels

  // Helper to get badge color
  const getComplianceBadgeColor = (level) => {
    switch (level) {
      case 'SOC2': return 'bg-red-600 text-white'
      case 'Internal': return 'bg-yellow-600 text-gray-900'
      case 'Public': return 'bg-green-600 text-white'
      default: return 'bg-gray-500 text-white'
    }
  }

  // Apply filtering logic based on compliance level and search query
  const filteredDataSources = useMemo(() => {
    let sources = ragSources

    // Filter by compliance level
    if (complianceLevelsEnabled && complianceLevelFilter) {
      sources = sources.filter(source =>
        source.complianceLevel && source.complianceLevel === complianceLevelFilter
      )
    }

    // Filter by search query
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase()
      sources = sources.filter(source => {
        const name = (source.name || '').toLowerCase()
        const serverName = (source.serverDisplayName || source.serverName || '').toLowerCase()
        return name.includes(query) || serverName.includes(query)
      })
    }

    return sources
  }, [ragSources, complianceLevelFilter, complianceLevelsEnabled, searchQuery])

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
        fixed left-0 top-0 h-full w-80 bg-gray-800 border-r border-gray-700 z-50 transform transition-transform duration-300 ease-in-out flex flex-col
        ${isOpen ? 'translate-x-0' : '-translate-x-full'}
        lg:relative lg:translate-x-0 lg:w-96
        ${!isOpen ? 'lg:hidden' : ''}
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

        {/* Search */}
        <div className="p-4 border-b border-gray-700 flex-shrink-0">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder="Search data sources..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-700 border border-gray-600 rounded-lg text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-200"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        {/* Data Sources List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-4 min-h-0">
          {/* Help text */}
          <div className="text-xs text-gray-400 mb-3 pb-3 border-b border-gray-700">
            Click to enable/disable. <span className="text-green-400">Green</span> = enabled.
            {selectedDataSources.size > 0 && (
              <span className="ml-2 text-blue-400">({selectedDataSources.size} selected)</span>
            )}
          </div>

          {filteredDataSources.length === 0 ? (
            <div className="text-gray-400 text-center py-8">No data sources available</div>
          ) : (
            <div className="space-y-3">
              {filteredDataSources.map(dataSource => {
                const selectionKey = `${dataSource.serverName}:${dataSource.id}`
                const isSelected = selectedDataSources.has(selectionKey)

                return (
                  <div
                    key={selectionKey}
                    onClick={() => toggleDataSource(selectionKey)}
                    className={`p-4 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-green-700 border-green-600 text-white'
                        : 'bg-gray-700 border-gray-600 text-gray-200 hover:bg-gray-600'
                    }`}
                  >
                    <div className="font-medium flex items-center justify-between">
                      <span>
                        {dataSource.name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                      </span>
                      {complianceLevelsEnabled && dataSource.complianceLevel && (
                        <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${getComplianceBadgeColor(dataSource.complianceLevel)}`}>
                          {dataSource.complianceLevel}
                        </span>
                      )}
                    </div>
                    <div className="text-sm mt-1 opacity-80">
                      {dataSource.serverDisplayName}
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
