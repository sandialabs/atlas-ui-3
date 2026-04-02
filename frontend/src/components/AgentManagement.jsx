import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Play, Square, Trash2, RefreshCw,
  Shield, Bot, Server, Activity, Clock, ChevronDown,
  ChevronRight, AlertTriangle, CheckCircle, XCircle,
  Cpu, Lock, Unlock, FileText, Wrench, Loader
} from 'lucide-react'

const STATUS_CONFIG = {
  queued:    { color: 'text-yellow-400 bg-yellow-900/30 border-yellow-600/40', icon: Clock,        label: 'Queued',    animate: false },
  scheduled: { color: 'text-blue-400 bg-blue-900/30 border-blue-600/40',     icon: Loader,       label: 'Scheduled', animate: true },
  running:   { color: 'text-green-400 bg-green-900/30 border-green-600/40',  icon: Activity,     label: 'Running',   animate: true },
  completed: { color: 'text-gray-400 bg-gray-800/50 border-gray-600/40',     icon: CheckCircle,  label: 'Completed', animate: false },
  stopped:   { color: 'text-gray-400 bg-gray-800/50 border-gray-600/40',     icon: XCircle,      label: 'Stopped',   animate: false },
  error:     { color: 'text-red-400 bg-red-900/30 border-red-600/40',        icon: AlertTriangle,label: 'Error',     animate: false },
}

const SANDBOX_COLORS = {
  restrictive: 'text-red-400 bg-red-900/20',
  standard: 'text-yellow-400 bg-yellow-900/20',
  hpc: 'text-purple-400 bg-purple-900/20',
  permissive: 'text-green-400 bg-green-900/20',
}

const AgentManagement = () => {
  const navigate = useNavigate()
  const [agents, setAgents] = useState([])
  const [templates, setTemplates] = useState([])
  const [availableTools, setAvailableTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selectedAgent, setSelectedAgent] = useState(null)
  const [launchModalOpen, setLaunchModalOpen] = useState(false)
  const [selectedTemplate, setSelectedTemplate] = useState(null)
  const [launchName, setLaunchName] = useState('')
  const [launchTask, setLaunchTask] = useState('')
  const [selectedTools, setSelectedTools] = useState([])
  const [launching, setLaunching] = useState(false)
  const [notification, setNotification] = useState(null)

  const showNotification = useCallback((message, type = 'info') => {
    setNotification({ message, type })
    setTimeout(() => setNotification(null), 5000)
  }, [])

  const fetchAgents = useCallback(async () => {
    try {
      const resp = await fetch('/api/agents/')
      if (!resp.ok) {
        if (resp.status === 403) {
          setError('Access denied. You need permission to manage agents.')
          return
        }
        throw new Error(`HTTP ${resp.status}`)
      }
      const data = await resp.json()
      setAgents(data.agents || [])
    } catch (err) {
      console.error('Error fetching agents:', err)
    }
  }, [])

  const fetchTemplates = useCallback(async () => {
    try {
      const resp = await fetch('/api/agents/templates')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = await resp.json()
      setTemplates(data.templates || [])
    } catch (err) {
      console.error('Error fetching templates:', err)
    }
  }, [])

  const fetchTools = useCallback(async () => {
    try {
      const resp = await fetch('/api/agents/tools')
      if (resp.ok) {
        const data = await resp.json()
        setAvailableTools(data.tools || [])
      }
    } catch {
      // Tools endpoint may not be available
    }
  }, [])

  const loadAll = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchAgents(), fetchTemplates(), fetchTools()])
    setLoading(false)
  }, [fetchAgents, fetchTemplates, fetchTools])

  useEffect(() => {
    loadAll()
    const interval = setInterval(fetchAgents, 5000)
    return () => clearInterval(interval)
  }, [loadAll, fetchAgents])

  const openLaunchModal = (template = null) => {
    setSelectedTemplate(template)
    setLaunchName('')
    setLaunchTask('')
    setSelectedTools(template?.mcp_servers || [])
    setLaunchModalOpen(true)
  }

  const toggleTool = (server) => {
    setSelectedTools(prev =>
      prev.includes(server)
        ? prev.filter(s => s !== server)
        : [...prev, server]
    )
  }

  const launchAgent = async () => {
    if (!selectedTemplate) return
    setLaunching(true)
    try {
      const resp = await fetch('/api/agents/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: selectedTemplate.id,
          name: launchName || undefined,
          task: launchTask || undefined,
          mcp_servers: selectedTools.length > 0 ? selectedTools : undefined,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      showNotification(`Agent "${data.agent.name}" launched`, 'success')
      setLaunchModalOpen(false)
      setLaunchName('')
      setLaunchTask('')
      setSelectedTools([])
      setSelectedTemplate(null)
      await fetchAgents()
    } catch (err) {
      showNotification(`Launch failed: ${err.message}`, 'error')
    } finally {
      setLaunching(false)
    }
  }

  const stopAgent = async (agentId) => {
    try {
      const resp = await fetch(`/api/agents/${agentId}/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId }),
      })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      showNotification('Agent stopped', 'success')
      await fetchAgents()
    } catch (err) {
      showNotification(`Stop failed: ${err.message}`, 'error')
    }
  }

  const deleteAgent = async (agentId) => {
    try {
      const resp = await fetch(`/api/agents/${agentId}`, { method: 'DELETE' })
      if (!resp.ok) {
        const err = await resp.json()
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      showNotification('Agent deleted', 'success')
      setSelectedAgent(null)
      await fetchAgents()
    } catch (err) {
      showNotification(`Delete failed: ${err.message}`, 'error')
    }
  }

  const runningCount = agents.filter(a => ['running', 'scheduled', 'queued'].includes(a.status)).length

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-200 flex items-center justify-center">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-4" />
          <p>Loading agent portal...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-200 flex items-center justify-center">
        <div className="text-center max-w-md">
          <Shield className="w-16 h-16 text-red-400 mx-auto mb-4" />
          <h2 className="text-xl font-bold mb-2">Access Denied</h2>
          <p className="text-gray-400 mb-6">{error}</p>
          <button onClick={() => navigate('/')} className="flex items-center gap-2 mx-auto px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">
            <ArrowLeft className="w-4 h-4" /> Back to Chat
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-200 overflow-y-auto">
      <div className="max-w-7xl mx-auto p-6">
        {/* Header */}
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <Bot className="w-8 h-8 text-blue-400" />
              <h1 className="text-2xl font-bold">Agent Portal</h1>
            </div>
            <div className="flex items-center gap-3">
              <button onClick={loadAll} className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors text-sm">
                <RefreshCw className="w-4 h-4" /> Refresh
              </button>
              <button onClick={() => navigate('/')} className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors text-sm">
                Back to Chat
              </button>
            </div>
          </div>
          <p className="text-gray-400">Launch, monitor, and control persistent AI agents with fine-grained access control</p>
        </div>

        {/* Agent Templates */}
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              <Cpu className="w-5 h-5 text-purple-400" /> Agent Templates
            </h2>
            <button onClick={() => openLaunchModal(null)} className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors">
              <Play className="w-4 h-4" /> Launch Agent
            </button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {templates.map((t) => (
              <div
                key={t.id}
                className={`border rounded-lg p-4 cursor-pointer transition-colors ${
                  t.can_launch
                    ? 'border-gray-600 hover:border-blue-500 hover:bg-gray-700/50'
                    : 'border-gray-700 opacity-50 cursor-not-allowed'
                }`}
                onClick={() => t.can_launch && openLaunchModal(t)}
              >
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-medium text-sm">{t.name}</h3>
                  {t.can_launch ? <Unlock className="w-4 h-4 text-green-400" /> : <Lock className="w-4 h-4 text-red-400" />}
                </div>
                <p className="text-gray-400 text-xs mb-3">{t.description}</p>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`text-xs px-2 py-0.5 rounded ${SANDBOX_COLORS[t.sandbox_policy] || 'text-gray-400 bg-gray-700'}`}>
                    {t.sandbox_policy}
                  </span>
                  <span className="text-xs text-gray-500">{t.max_steps} steps max</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Agent Queue / Active Agents */}
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <h2 className="text-lg font-semibold flex items-center gap-2 mb-4">
            <Activity className="w-5 h-5 text-green-400" />
            Agent Queue
            <span className="text-sm text-gray-400 font-normal">
              ({runningCount} active / {agents.length} total)
            </span>
          </h2>

          {agents.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <Bot className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p>No agents yet. Launch one from the templates above.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {agents.map((agent) => {
                const cfg = STATUS_CONFIG[agent.status] || STATUS_CONFIG.stopped
                const StatusIcon = cfg.icon
                const isSelected = selectedAgent?.id === agent.id

                return (
                  <div key={agent.id} className={`border rounded-lg transition-colors ${cfg.color} ${isSelected ? 'ring-1 ring-blue-500' : ''}`}>
                    <div className="flex items-center justify-between p-4 cursor-pointer" onClick={() => setSelectedAgent(isSelected ? null : agent)}>
                      <div className="flex items-center gap-3">
                        {isSelected ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                        <StatusIcon className={`w-5 h-5 ${cfg.animate ? 'animate-spin' : ''}`} />
                        <div>
                          <div className="flex items-center gap-2">
                            <h3 className="font-medium">{agent.name}</h3>
                            <span className={`text-xs px-2 py-0.5 rounded ${cfg.color.split(' ').slice(0,2).join(' ')}`}>
                              {cfg.label}
                            </span>
                          </div>
                          <p className="text-xs text-gray-400">
                            {agent.id} | {agent.loop_strategy} | {agent.steps_completed}/{agent.max_steps} steps
                            {agent.task && <span className="ml-2 text-gray-500">| "{agent.task.substring(0, 60)}{agent.task.length > 60 ? '...' : ''}"</span>}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className={`text-xs px-2 py-0.5 rounded ${SANDBOX_COLORS[agent.sandbox_policy] || ''}`}>
                          {agent.sandbox_policy}
                        </span>
                        <span className="text-xs text-gray-400 flex items-center gap-1">
                          <Server className="w-3 h-3" /> {agent.mcp_servers?.length || 0} tools
                        </span>
                        {['running', 'scheduled', 'queued'].includes(agent.status) && (
                          <button onClick={(e) => { e.stopPropagation(); stopAgent(agent.id) }}
                            className="p-1.5 bg-red-600/20 hover:bg-red-600/40 rounded transition-colors" title="Stop agent">
                            <Square className="w-3 h-3 text-red-400" />
                          </button>
                        )}
                        <button onClick={(e) => { e.stopPropagation(); deleteAgent(agent.id) }}
                          className="p-1.5 bg-gray-600/20 hover:bg-gray-600/40 rounded transition-colors" title="Delete agent">
                          <Trash2 className="w-3 h-3 text-gray-400" />
                        </button>
                      </div>
                    </div>

                    {/* Expanded execution detail */}
                    {isSelected && (
                      <div className="border-t border-gray-700 p-4 bg-gray-800/50">
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm mb-4">
                          <div>
                            <span className="text-gray-500">Owner</span>
                            <p>{agent.owner}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">Template</span>
                            <p>{agent.template_id}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">Created</span>
                            <p>{new Date(agent.created_at).toLocaleString()}</p>
                          </div>
                          <div>
                            <span className="text-gray-500">Last Activity</span>
                            <p>{new Date(agent.last_activity).toLocaleString()}</p>
                          </div>
                        </div>

                        {/* Task instructions */}
                        {agent.task && (
                          <div className="mb-4">
                            <span className="text-gray-500 text-sm flex items-center gap-1 mb-1">
                              <FileText className="w-3 h-3" /> Task Instructions
                            </span>
                            <div className="p-3 bg-gray-900 rounded-lg text-sm text-gray-300 whitespace-pre-wrap">
                              {agent.task}
                            </div>
                          </div>
                        )}

                        {/* Tools */}
                        {agent.mcp_servers?.length > 0 && (
                          <div className="mb-4">
                            <span className="text-gray-500 text-sm flex items-center gap-1 mb-1">
                              <Wrench className="w-3 h-3" /> Assigned Tools
                            </span>
                            <div className="flex gap-2 flex-wrap">
                              {agent.mcp_servers.map((s) => (
                                <span key={s} className="text-xs px-2 py-1 bg-gray-700 rounded">{s}</span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Prefect execution info */}
                        {agent.prefect && (
                          <div className="mb-4">
                            <span className="text-gray-500 text-sm flex items-center gap-1 mb-1">
                              <Activity className="w-3 h-3" /> Execution
                            </span>
                            <div className="p-3 bg-gray-900 rounded-lg text-sm">
                              <div className="grid grid-cols-2 gap-2">
                                <span>Flow Run: <code className="text-blue-400">{agent.prefect.flow_run_id?.substring(0, 12)}...</code></span>
                                <span>State: <span className={
                                  agent.prefect.state === 'COMPLETED' ? 'text-green-400' :
                                  agent.prefect.state === 'RUNNING' ? 'text-blue-400' :
                                  agent.prefect.state === 'FAILED' ? 'text-red-400' :
                                  'text-yellow-400'
                                }>{agent.prefect.state || 'SCHEDULED'}</span></span>
                              </div>
                            </div>
                          </div>
                        )}

                        {/* Execution timeline */}
                        <div>
                          <span className="text-gray-500 text-sm flex items-center gap-1 mb-1">
                            <Clock className="w-3 h-3" /> Timeline
                          </span>
                          <div className="flex items-center gap-2 text-xs text-gray-400">
                            <span className="px-2 py-1 bg-gray-700 rounded">Created {new Date(agent.created_at).toLocaleTimeString()}</span>
                            {agent.started_at && <span className="text-gray-600">&rarr;</span>}
                            {agent.started_at && <span className="px-2 py-1 bg-gray-700 rounded">Started {new Date(agent.started_at).toLocaleTimeString()}</span>}
                            {agent.stopped_at && <span className="text-gray-600">&rarr;</span>}
                            {agent.stopped_at && (
                              <span className="px-2 py-1 bg-gray-700 rounded">
                                Stopped {new Date(agent.stopped_at).toLocaleTimeString()}
                                {agent.stopped_by && ` by ${agent.stopped_by}`}
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Launch Modal */}
      {launchModalOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4"
          onClick={() => { setLaunchModalOpen(false); setSelectedTemplate(null) }}>
          <div className="bg-gray-800 rounded-lg p-6 max-w-2xl w-full max-h-[90vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}>
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Play className="w-5 h-5 text-blue-400" /> Launch Agent
            </h2>

            {/* Template selection */}
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">Template</label>
              <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
                {templates.filter(t => t.can_launch).map((t) => (
                  <button key={t.id} onClick={() => { setSelectedTemplate(t); setSelectedTools(t.mcp_servers || []) }}
                    className={`text-left p-3 rounded-lg border transition-colors ${
                      selectedTemplate?.id === t.id ? 'border-blue-500 bg-blue-900/20' : 'border-gray-600 hover:border-gray-500'
                    }`}>
                    <div className="font-medium text-sm">{t.name}</div>
                    <div className="text-xs text-gray-400 mt-0.5">{t.description}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Task instructions */}
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2 flex items-center gap-1">
                <FileText className="w-3 h-3" /> Task Instructions
              </label>
              <textarea
                value={launchTask}
                onChange={(e) => setLaunchTask(e.target.value)}
                placeholder="Describe what the agent should do..."
                rows={4}
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm focus:outline-none focus:border-blue-500 resize-y"
              />
            </div>

            {/* Name override */}
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">Name (optional)</label>
              <input type="text" value={launchName} onChange={(e) => setLaunchName(e.target.value)}
                placeholder="Custom agent name..."
                className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm focus:outline-none focus:border-blue-500" />
            </div>

            {/* Tool picker */}
            {availableTools.length > 0 && (
              <div className="mb-4">
                <label className="block text-sm text-gray-400 mb-2 flex items-center gap-1">
                  <Wrench className="w-3 h-3" /> MCP Tools
                  <span className="text-xs text-gray-500 ml-1">({selectedTools.length} selected)</span>
                </label>
                <div className="grid grid-cols-2 gap-2 max-h-36 overflow-y-auto p-1">
                  {availableTools.map((tool) => (
                    <label key={tool.server}
                      className={`flex items-center gap-2 p-2 rounded-lg border cursor-pointer transition-colors text-sm ${
                        selectedTools.includes(tool.server) ? 'border-blue-500 bg-blue-900/20' : 'border-gray-600 hover:border-gray-500'
                      }`}>
                      <input type="checkbox" checked={selectedTools.includes(tool.server)}
                        onChange={() => toggleTool(tool.server)}
                        className="rounded bg-gray-700 border-gray-600" />
                      <Server className="w-3 h-3 text-gray-400" />
                      <span>{tool.server}</span>
                      {tool.authorized && <CheckCircle className="w-3 h-3 text-green-400 ml-auto" />}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* Template details */}
            {selectedTemplate && (
              <div className="mb-4 p-3 bg-gray-900 rounded-lg text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <div><span className="text-gray-500">Strategy:</span> {selectedTemplate.loop_strategy}</div>
                  <div><span className="text-gray-500">Max steps:</span> {selectedTemplate.max_steps}</div>
                  <div>
                    <span className="text-gray-500">Sandbox:</span>{' '}
                    <span className={`px-1.5 py-0.5 rounded text-xs ${SANDBOX_COLORS[selectedTemplate.sandbox_policy] || ''}`}>
                      {selectedTemplate.sandbox_policy}
                    </span>
                  </div>
                  <div><span className="text-gray-500">Tools:</span> {selectedTools.join(', ') || 'none'}</div>
                </div>
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-3">
              <button onClick={() => { setLaunchModalOpen(false); setSelectedTemplate(null) }}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors text-sm">
                Cancel
              </button>
              <button onClick={launchAgent} disabled={!selectedTemplate || launching}
                className={`px-4 py-2 rounded-lg transition-colors text-sm flex items-center gap-2 ${
                  selectedTemplate && !launching ? 'bg-blue-600 hover:bg-blue-700' : 'bg-gray-600 cursor-not-allowed opacity-50'
                }`}>
                {launching ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                {launching ? 'Launching...' : 'Launch'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Notification toast */}
      {notification && (
        <div className={`fixed bottom-6 right-6 px-4 py-3 rounded-lg shadow-lg z-50 ${
          notification.type === 'error'
            ? 'bg-red-900/90 text-red-200 border border-red-600/40'
            : 'bg-green-900/90 text-green-200 border border-green-600/40'
        }`}>
          {notification.message}
        </div>
      )}
    </div>
  )
}

export default AgentManagement
