import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bot, Shield, CheckCircle, AlertTriangle } from 'lucide-react'

const AgentPortalCard = () => {
  const navigate = useNavigate()
  const [agentCount, setAgentCount] = useState(0)
  const [cerbosHealthy, setCerbosHealthy] = useState(null)

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const [agentsResp, cerbosResp] = await Promise.all([
          fetch('/api/agents/').catch(() => null),
          fetch('/api/agents/cerbos/status').catch(() => null),
        ])
        if (agentsResp?.ok) {
          const data = await agentsResp.json()
          setAgentCount(data.agents?.filter(a => a.status === 'running').length || 0)
        }
        if (cerbosResp?.ok) {
          const data = await cerbosResp.json()
          setCerbosHealthy(data.healthy)
        }
      } catch (err) {
        console.error('Error fetching agent portal status:', err)
      }
    }
    fetchStatus()
  }, [])

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <Bot className="w-6 h-6 text-blue-400" />
        <h2 className="text-lg font-semibold">Agent Portal</h2>
      </div>
      <p className="text-gray-400 mb-4">
        Launch and manage persistent AI agents with Cerbos policy-based access control.
      </p>
      <div className="flex items-center gap-4 mb-4">
        <div className="text-sm">
          <span className="text-gray-500">Running: </span>
          <span className="text-green-400 font-medium">{agentCount}</span>
        </div>
        <div className="text-sm flex items-center gap-1">
          <Shield className="w-3 h-3" />
          <span className="text-gray-500">Cerbos: </span>
          {cerbosHealthy === true && (
            <span className="text-green-400 flex items-center gap-1">
              <CheckCircle className="w-3 h-3" /> Active
            </span>
          )}
          {cerbosHealthy === false && (
            <span className="text-yellow-400 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> Fallback
            </span>
          )}
          {cerbosHealthy === null && (
            <span className="text-gray-500">--</span>
          )}
        </div>
      </div>
      <button
        onClick={() => navigate('/admin/agents')}
        className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
      >
        Open Agent Portal
      </button>
    </div>
  )
}

export default AgentPortalCard
