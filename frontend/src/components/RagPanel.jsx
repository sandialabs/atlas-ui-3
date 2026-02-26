import { useMemo, useState, useCallback } from 'react'
import { X, Search, CheckSquare, Square } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const RagPanel = ({ isOpen, onClose }) => {
  const {
    ragSources,
    selectedDataSources,
    toggleDataSource,
    addDataSources,
    clearDataSources,
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
        const label = (source.label || source.name || '').toLowerCase()
        const description = (source.description || '').toLowerCase()
        const serverName = (source.serverDisplayName || source.serverName || '').toLowerCase()
        return label.includes(query) || description.includes(query) || serverName.includes(query)
      })
    }

    return sources
  }, [ragSources, complianceLevelFilter, complianceLevelsEnabled, searchQuery])

  // Enable all filtered data sources
  const enableAll = useCallback(() => {
    const keys = filteredDataSources.map(ds => `${ds.serverName}:${ds.id}`)
    addDataSources(keys)
  }, [filteredDataSources, addDataSources])

  // Clear all selected data sources (clears everything, not just filtered)
  const clearAll = useCallback(() => {
    clearDataSources()
  }, [clearDataSources])

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

          {/* Enable All / Clear All buttons */}
          <div className="flex gap-2 mt-3">
            <button
              onClick={enableAll}
              disabled={filteredDataSources.length === 0}
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-green-700 hover:bg-green-600 disabled:bg-gray-600 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              <CheckSquare className="w-4 h-4" />
              Enable All
            </button>
            <button
              onClick={clearAll}
              disabled={selectedDataSources.size === 0}
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-gray-600 hover:bg-gray-500 disabled:bg-gray-700 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              <Square className="w-4 h-4" />
              Clear All
            </button>
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
            <div className="space-y-1.5">
              {filteredDataSources.map(dataSource => {
                const selectionKey = `${dataSource.serverName}:${dataSource.id}`
                const isSelected = selectedDataSources.has(selectionKey)
                const displayLabel = dataSource.label || dataSource.name || dataSource.id

                return (
                  <div
                    key={selectionKey}
                    onClick={() => toggleDataSource(selectionKey)}
                    className={`px-3 py-2 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-green-700 border-green-600 text-white'
                        : 'bg-gray-700 border-gray-600 text-gray-200 hover:bg-gray-600'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-sm truncate">
                        {displayLabel}
                      </span>
                      {complianceLevelsEnabled && dataSource.complianceLevel && (
                        <span className={`px-1.5 py-0.5 text-xs font-semibold rounded-full whitespace-nowrap ${getComplianceBadgeColor(dataSource.complianceLevel)}`}>
                          {dataSource.complianceLevel}
                        </span>
                      )}
                    </div>
                    {dataSource.description && (
                      <div className={`text-xs mt-0.5 line-clamp-2 ${isSelected ? 'opacity-80' : 'text-gray-400'}`}>
                        {dataSource.description}
                      </div>
                    )}
                    <div className={`text-xs mt-0.5 ${isSelected ? 'opacity-60' : 'text-gray-500'}`}>
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
