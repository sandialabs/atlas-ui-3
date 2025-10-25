import React from 'react'
import { Settings } from 'lucide-react'

const MCPConfigurationCard = ({ openModal, addNotification, systemStatus }) => {
  const manageMCP = async () => {
    try {
      const response = await fetch('/admin/mcp-config')
      const data = await response.json()
      
      openModal('Edit MCP Configuration', {
        type: 'textarea',
        value: data.content,
        description: 'Configure MCP servers and their properties. Changes require application restart.'
      }, 'mcp-config')
    } catch (err) {
      addNotification('Error loading MCP configuration: ' + err.message, 'error')
    }
  }

  const getStatusColor = (status) => {
    switch (status) {
      case 'healthy': return 'text-green-400 bg-green-900/20'
      case 'warning': return 'text-yellow-400 bg-yellow-900/20'
      case 'error': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <Settings className="w-6 h-6 text-purple-400" />
        <h2 className="text-lg font-semibold">MCP Configuration</h2>
      </div>
      <p className="text-gray-400 mb-4">Configure MCP servers and their settings.</p>
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor(systemStatus.overall_status || 'healthy')}`}>
        {systemStatus.overall_status || 'Ready'}
      </div>
      <button 
        onClick={manageMCP}
        className="w-full px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors"
      >
        Edit MCP Config
      </button>
    </div>
  )
}

export default MCPConfigurationCard
