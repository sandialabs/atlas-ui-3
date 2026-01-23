import { useMemo } from 'react'
import { X } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const RagPanel = ({ isOpen, onClose }) => {
  const {
    ragSources,
    selectedDataSources,
    toggleDataSource,
    features,
    complianceLevelFilter
  } = useChat()

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

  // Apply filtering logic based on compliance level from header
  const filteredDataSources = useMemo(() => {
    if (!complianceLevelsEnabled || !complianceLevelFilter) {
      return ragSources
    }
    return ragSources.filter(source =>
      source.complianceLevel && source.complianceLevel === complianceLevelFilter
    )
  }, [ragSources, complianceLevelFilter, complianceLevelsEnabled])

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

        {/* Data Sources List */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-4 min-h-0">
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
