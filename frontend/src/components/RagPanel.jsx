import { useMemo, useState, useEffect, useRef } from 'react'
import { X, Save } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const RagPanel = ({ isOpen, onClose }) => {
  const prevOpenRef = useRef(false)
  const {
    ragSources, // Use rich source data
    selectedDataSources: savedSelectedDataSources,
    toggleDataSource: saveToggleDataSource,
    onlyRag: savedOnlyRag,
    setOnlyRag: saveSetOnlyRag,
    features,
    complianceLevelFilter
  } = useChat()

  const complianceLevelsEnabled = features.compliance_levels
  
  // Local state for pending changes
  const [pendingSelectedDataSources, setPendingSelectedDataSources] = useState(new Set())
  const [pendingOnlyRag, setPendingOnlyRag] = useState(false)
  const [hasChanges, setHasChanges] = useState(false)
  
  // Initialize pending state from saved state only when panel transitions from closed to open
  useEffect(() => {
    if (isOpen && !prevOpenRef.current) {
      setPendingSelectedDataSources(new Set(savedSelectedDataSources))
      setPendingOnlyRag(savedOnlyRag)
      setHasChanges(false)
    }
    prevOpenRef.current = isOpen
  }, [isOpen, savedSelectedDataSources, savedOnlyRag])
  
  // Use pending state while editing
  const selectedDataSources = pendingSelectedDataSources
  const onlyRag = pendingOnlyRag
  
  // Toggle function that works with pending state
  const toggleDataSource = (sourceKey) => {
    setPendingSelectedDataSources(prev => {
      const next = new Set(prev)
      if (next.has(sourceKey)) {
        next.delete(sourceKey)
      } else {
        next.add(sourceKey)
      }
      setHasChanges(true)
      return next
    })
  }
  
  const setOnlyRag = (value) => {
    setPendingOnlyRag(value)
    setHasChanges(true)
  }
  
  // Save handler - commits pending changes to context
  const handleSave = () => {
    // Update data sources
    const sourcesToAdd = Array.from(pendingSelectedDataSources).filter(s => !savedSelectedDataSources.has(s))
    const sourcesToRemove = Array.from(savedSelectedDataSources).filter(s => !pendingSelectedDataSources.has(s))
    
    sourcesToAdd.forEach(s => saveToggleDataSource(s))
    sourcesToRemove.forEach(s => saveToggleDataSource(s))
    
    // Update onlyRag if changed
    if (pendingOnlyRag !== savedOnlyRag) {
      saveSetOnlyRag(pendingOnlyRag)
    }
    
    setHasChanges(false)
    onClose()
  }
  
  // Cancel handler - reverts pending changes
  const handleCancel = () => {
    setPendingSelectedDataSources(new Set(savedSelectedDataSources))
    setPendingOnlyRag(savedOnlyRag)
    setHasChanges(false)
    onClose()
  }
  
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
            onClick={handleCancel}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* RAG Controls */}
        <div className="p-4 border-b border-gray-700 space-y-3 flex-shrink-0">
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
        <div className="flex-1 overflow-y-auto custom-scrollbar p-4 min-h-0">
          {filteredDataSources.length === 0 ? (
            <div className="text-gray-400 text-center py-8">No data sources available</div>
          ) : (
            <div className="space-y-3">
              {filteredDataSources.map(dataSource => {
                // Use just the data source ID without server prefix
                // Server grouping is handled by the UI structure, not the selection key
                const selectionKey = dataSource.id
                const isSelected = selectedDataSources.has(selectionKey)
                
                return (
                  <div
                    key={selectionKey}
                    onClick={() => toggleDataSource(selectionKey)}
                    className={`p-4 rounded-lg border cursor-pointer transition-colors ${
                      isSelected
                        ? 'bg-blue-600 border-blue-500 text-white'
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
                      {dataSource.serverDisplayName} - Click to {isSelected ? 'deselect' : 'select'}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
        
        {/* Footer with Save/Cancel buttons */}
        <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-gray-700 flex-shrink-0">
          <button
            onClick={handleCancel}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors font-medium ${
              hasChanges
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'bg-gray-600 text-gray-400 cursor-not-allowed'
            }`}
          >
            <Save className="w-4 h-4" />
            Save Changes
          </button>
        </div>
      </aside>
    </>
  )
}

export default RagPanel
