import React from 'react'
import { Database } from 'lucide-react'

const LLMConfigurationCard = ({ openModal, addNotification }) => {
  const manageLLM = async () => {
    try {
      const response = await fetch('/admin/llm-config')
      const data = await response.json()
      
      openModal('Edit LLM Configuration', {
        type: 'textarea',
        value: data.content,
        description: 'Configure language models and their endpoints. Changes take effect immediately.'
      }, 'llm-config')
    } catch (err) {
      addNotification('Error loading LLM configuration: ' + err.message, 'error')
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
        <Database className="w-6 h-6 text-green-400" />
        <h2 className="text-lg font-semibold">LLM Configuration</h2>
      </div>
      <p className="text-gray-400 mb-4">Manage language model settings and endpoints.</p>
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor('healthy')}`}>
        Ready
      </div>
      <button 
        onClick={manageLLM}
        className="w-full px-4 py-2 bg-green-600 hover:bg-green-700 rounded-lg transition-colors"
      >
        Edit LLM Config
      </button>
    </div>
  )
}

export default LLMConfigurationCard
