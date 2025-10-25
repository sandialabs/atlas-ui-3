import React, { useState, useRef, useEffect } from 'react'
import { Settings, ChevronDown, ChevronRight, CheckCircle, XCircle, X } from 'lucide-react'

const ConfigViewerModal = ({ isOpen, onClose, addNotification }) => {
  const [configs, setConfigs] = useState(null)
  const [loading, setLoading] = useState(false)
  const [expandedSections, setExpandedSections] = useState({})
  const modalRef = useRef(null)

  const loadConfigs = async () => {
    setLoading(true)
    try {
      const response = await fetch('/admin/config/view')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      setConfigs(data)
      // Expand app_settings by default
      setExpandedSections({ app_settings: true })
    } catch (err) {
      addNotification('Error loading configurations: ' + err.message, 'error')
    } finally {
      setLoading(false)
    }
  }

  // Load configs when modal opens
  useEffect(() => {
    if (isOpen && !configs) {
      loadConfigs()
    }
  }, [isOpen])

  // Handle click outside to close
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (modalRef.current && !modalRef.current.contains(event.target)) {
        onClose()
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen, onClose])

  // Handle escape key to close
  useEffect(() => {
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    if (isOpen) {
      document.addEventListener('keydown', handleEscape)
      return () => document.removeEventListener('keydown', handleEscape)
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  const toggleSection = (section) => {
    setExpandedSections(prev => ({
      ...prev,
      [section]: !prev[section]
    }))
  }

  const renderValue = (value, key = '') => {
    if (value === null || value === undefined) {
      return <span className="text-gray-500 italic">null</span>
    }
    
    if (typeof value === 'boolean') {
      return (
        <span className={`font-medium ${value ? 'text-green-400' : 'text-red-400'}`}>
          {value.toString()}
        </span>
      )
    }
    
    if (typeof value === 'number') {
      return <span className="text-blue-400">{value}</span>
    }
    
    if (typeof value === 'string') {
      const isMasked = value === '***MASKED***'
      return (
        <span className={`${isMasked ? 'text-yellow-400 font-mono' : 'text-gray-300'}`}>
          "{value}"
        </span>
      )
    }
    
    if (Array.isArray(value)) {
      if (value.length === 0) {
        return <span className="text-gray-500 italic">[]</span>
      }
      return (
        <div className="ml-4">
          <span className="text-gray-400">[</span>
          {value.map((item, index) => (
            <div key={index} className="ml-4">
              <span className="text-gray-500">{index}:</span> {renderValue(item)}
              {index < value.length - 1 && <span className="text-gray-400">,</span>}
            </div>
          ))}
          <span className="text-gray-400">]</span>
        </div>
      )
    }
    
    if (typeof value === 'object') {
      const entries = Object.entries(value)
      if (entries.length === 0) {
        return <span className="text-gray-500 italic">{}</span>
      }
      return (
        <div className="ml-4">
          <span className="text-gray-400">{'{'}</span>
          {entries.map(([objKey, objValue], index) => (
            <div key={objKey} className="ml-4">
              <span className="text-cyan-400">"{objKey}"</span>
              <span className="text-gray-400">: </span>
              {renderValue(objValue, objKey)}
              {index < entries.length - 1 && <span className="text-gray-400">,</span>}
            </div>
          ))}
          <span className="text-gray-400">{'}'}</span>
        </div>
      )
    }
    
    return <span className="text-gray-300">{String(value)}</span>
  }

  const renderSection = (title, data, validationStatus) => {
    const isExpanded = expandedSections[title]
    const sectionKey = title.toLowerCase().replace(' ', '_')
    const isValid = validationStatus?.[sectionKey]
    
    return (
      <div key={title} className="border border-gray-600 rounded-lg mb-4">
        <button
          onClick={() => toggleSection(title)}
          className="w-full flex items-center justify-between p-4 text-left hover:bg-gray-700 transition-colors"
        >
          <div className="flex items-center gap-3">
            {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
            <h3 className="text-lg font-semibold text-white">{title.replace('_', ' ').toUpperCase()}</h3>
            {isValid !== undefined && (
              <div className="flex items-center gap-1">
                {isValid ? (
                  <CheckCircle size={16} className="text-green-400" />
                ) : (
                  <XCircle size={16} className="text-red-400" />
                )}
                <span className={`text-sm ${isValid ? 'text-green-400' : 'text-red-400'}`}>
                  {isValid ? 'Valid' : 'Invalid/Empty'}
                </span>
              </div>
            )}
          </div>
          <span className="text-gray-400 text-sm">
            {typeof data === 'object' && data !== null ? 
              Object.keys(data).length + ' items' : 
              '1 item'}
          </span>
        </button>
        
        {isExpanded && (
          <div className="border-t border-gray-600 p-4 bg-gray-800 font-mono text-sm">
            {renderValue(data)}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div 
        ref={modalRef}
        className="bg-gray-800 rounded-lg w-full max-w-6xl max-h-[90vh] flex flex-col"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-600">
          <div className="flex items-center gap-3">
            <Settings className="w-6 h-6 text-blue-400" />
            <h2 className="text-xl font-semibold">Configuration Viewer</h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-700 rounded-lg transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          <p className="text-gray-400 mb-6">
            View all application configurations loaded from the config manager.
          </p>
          
          {!configs ? (
            <div className="flex items-center justify-center py-12">
              <button 
                onClick={loadConfigs}
                disabled={loading}
                className="px-6 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 rounded-lg transition-colors"
              >
                {loading ? 'Loading...' : 'Load Configurations'}
              </button>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="flex justify-between items-center">
                <span className="text-sm text-gray-400">
                  Last loaded: {new Date().toLocaleTimeString()}
                </span>
                <button 
                  onClick={loadConfigs}
                  disabled={loading}
                  className="px-4 py-2 text-sm bg-gray-600 hover:bg-gray-500 disabled:bg-gray-700 rounded transition-colors"
                >
                  {loading ? 'Refreshing...' : 'Refresh'}
                </button>
              </div>
              
              {configs.app_settings && renderSection('app_settings', configs.app_settings, configs.config_validation)}
              {configs.llm_config && renderSection('llm_config', configs.llm_config, configs.config_validation)}
              {configs.mcp_config && renderSection('mcp_config', configs.mcp_config, configs.config_validation)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

const ConfigViewerCard = ({ addNotification }) => {
  const [isModalOpen, setIsModalOpen] = useState(false)

  return (
    <>
      <div className="bg-gray-800 rounded-lg p-6">
        <div className="flex items-center gap-3 mb-4">
          <Settings className="w-6 h-6 text-blue-400" />
          <h2 className="text-lg font-semibold">Configuration Viewer</h2>
        </div>
        
        <p className="text-gray-400 mb-4">
          View all application configurations loaded from the config manager.
        </p>
        
        <div className="px-3 py-1 rounded text-sm font-medium mb-4 text-green-400 bg-green-900/20">
          Ready
        </div>
        
        <button 
          onClick={() => setIsModalOpen(true)}
          className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
        >
          View Configurations
        </button>
      </div>

      <ConfigViewerModal 
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        addNotification={addNotification}
      />
    </>
  )
}

export default ConfigViewerCard