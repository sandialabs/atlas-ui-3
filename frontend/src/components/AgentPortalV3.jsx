import React, { useEffect, useState, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Play,
  X,
  Trash2,
  RefreshCw,
  ChevronLeft,
  Server,
  Bot,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Terminal,
} from 'lucide-react'

const ACTIVE_STATUSES = new Set(['pending', 'launching', 'running'])

const statusIcon = (status) => {
  if (status === 'running' || status === 'launching' || status === 'pending')
    return <Loader2 className="w-4 h-4 animate-spin text-indigo-400" />
  if (status === 'succeeded') return <CheckCircle2 className="w-4 h-4 text-green-400" />
  if (status === 'failed' || status === 'cancelled') return <XCircle className="w-4 h-4 text-red-400" />
  return <Clock className="w-4 h-4 text-gray-400" />
}

const fmtTime = (iso) => {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const apiBase = '/api/agent-portal-v3'

function ConnInfo({ cap }) {
  if (!cap) return null
  return (
    <div className="flex items-center gap-3 text-xs text-gray-300">
      <span className="inline-flex items-center gap-1">
        <Server className="w-3.5 h-3.5" />
        {cap.cluster_reachable ? (
          <span className="text-green-400">cluster reachable</span>
        ) : (
          <span className="text-red-400">cluster unreachable</span>
        )}
      </span>
      <span>ns: <span className="text-gray-100">{cap.namespace}</span></span>
      <span>image: <span className="text-gray-100">{cap.image}</span></span>
      <span>providers: <span className="text-gray-100">{(cap.providers_configured || []).join(', ') || 'none'}</span></span>
    </div>
  )
}

function LaunchForm({ servers, models, onLaunch, busy }) {
  const [prompt, setPrompt] = useState('What is 17 * 23? Use a calculator if available, then explain.')
  const [displayName, setDisplayName] = useState('')
  const [selectedServers, setSelectedServers] = useState(new Set())
  const [modelChoice, setModelChoice] = useState('')

  useEffect(() => {
    if (!modelChoice && models.length > 0) {
      const def = models.find((m) => m.available) || models[0]
      if (def) setModelChoice(def.name)
    }
  }, [models, modelChoice])

  const toggleServer = (name) => {
    setSelectedServers((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const handleLaunch = () => {
    const m = models.find((x) => x.name === modelChoice)
    if (!m) return
    onLaunch({
      prompt,
      mcp_servers: Array.from(selectedServers),
      llm_model: m.model_id,
      llm_provider: m.provider,
      display_name: displayName,
    })
  }

  return (
    <div className="bg-gray-800/70 border border-gray-700 rounded-lg p-4 space-y-3">
      <h2 className="text-lg font-semibold flex items-center gap-2">
        <Play className="w-5 h-5" /> Launch a new agent
      </h2>
      <div>
        <label className="block text-xs text-gray-300 mb-1">Display name (optional)</label>
        <input
          type="text"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="e.g. nightly-report-gen"
          className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-indigo-500 outline-none"
        />
      </div>
      <div>
        <label className="block text-xs text-gray-300 mb-1">Model</label>
        <select
          value={modelChoice}
          onChange={(e) => setModelChoice(e.target.value)}
          className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm focus:border-indigo-500 outline-none"
        >
          {models.map((m) => (
            <option key={m.name} value={m.name} disabled={!m.available}>
              {m.name} ({m.provider}){m.available ? '' : ' — missing API key'}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="block text-xs text-gray-300 mb-1">
          MCP servers (only http/sse are selectable -- stdio servers are skipped because the agent runs in a sealed pod)
        </label>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-48 overflow-auto bg-gray-900 border border-gray-700 rounded p-2">
          {servers.length === 0 && (
            <div className="text-gray-500 text-sm">No MCP servers configured.</div>
          )}
          {servers.map((s) => (
            <label
              key={s.name}
              className={`flex items-start gap-2 p-2 rounded border ${
                s.selectable ? 'border-gray-700 hover:border-indigo-500' : 'border-gray-800 opacity-60'
              }`}
              title={s.selectable ? s.description : 'stdio servers can\'t be selected from the v3 portal'}
            >
              <input
                type="checkbox"
                disabled={!s.selectable}
                checked={selectedServers.has(s.name)}
                onChange={() => toggleServer(s.name)}
                className="mt-1"
              />
              <div className="min-w-0">
                <div className="font-medium text-sm truncate">{s.name}</div>
                <div className="text-xs text-gray-400 truncate">
                  {s.transport}
                  {s.url ? ` · ${s.url}` : ''}
                </div>
                {s.description && (
                  <div className="text-xs text-gray-500 truncate">{s.description}</div>
                )}
              </div>
            </label>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-xs text-gray-300 mb-1">Prompt</label>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={5}
          className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm focus:border-indigo-500 outline-none font-mono"
        />
      </div>
      <div className="flex justify-end">
        <button
          onClick={handleLaunch}
          disabled={busy || !prompt.trim() || !modelChoice}
          className="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Launch
        </button>
      </div>
    </div>
  )
}

function RunsTable({ runs, onSelect, selectedId, onCancel, onDelete }) {
  return (
    <div className="bg-gray-800/70 border border-gray-700 rounded-lg overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-700 text-sm font-medium flex items-center gap-2">
        <Bot className="w-4 h-4" /> Recent runs
      </div>
      <div className="overflow-x-auto max-h-[60vh]">
        <table className="w-full text-sm">
          <thead className="bg-gray-900 text-gray-400 text-xs uppercase">
            <tr>
              <th className="text-left px-3 py-2">Status</th>
              <th className="text-left px-3 py-2">Name</th>
              <th className="text-left px-3 py-2">Model</th>
              <th className="text-left px-3 py-2">Created</th>
              <th className="text-right px-3 py-2">Actions</th>
            </tr>
          </thead>
          <tbody>
            {runs.length === 0 && (
              <tr>
                <td colSpan={5} className="text-center text-gray-500 py-6">
                  No runs yet -- launch one above.
                </td>
              </tr>
            )}
            {runs.map((r) => (
              <tr
                key={r.id}
                onClick={() => onSelect(r.id)}
                className={`cursor-pointer border-t border-gray-700/50 hover:bg-gray-700/40 ${
                  selectedId === r.id ? 'bg-gray-700/60' : ''
                }`}
              >
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    {statusIcon(r.status)}
                    <span className="capitalize">{r.status}</span>
                  </div>
                </td>
                <td className="px-3 py-2">
                  <div className="font-medium truncate max-w-[18ch]" title={r.display_name}>{r.display_name || r.id.slice(0, 8)}</div>
                  <div className="text-xs text-gray-500 truncate max-w-[24ch]">{r.id}</div>
                </td>
                <td className="px-3 py-2 text-gray-300">{r.llm_model}</td>
                <td className="px-3 py-2 text-gray-300">{fmtTime(r.created_at)}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {ACTIVE_STATUSES.has(r.status) && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        onCancel(r.id)
                      }}
                      title="Cancel"
                      className="p-1.5 rounded hover:bg-yellow-700/50 inline-block"
                    >
                      <X className="w-4 h-4 text-yellow-400" />
                    </button>
                  )}
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      if (confirm('Delete this run?')) onDelete(r.id)
                    }}
                    title="Delete"
                    className="p-1.5 rounded hover:bg-red-700/50 inline-block ml-1"
                  >
                    <Trash2 className="w-4 h-4 text-red-400" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function RunDetail({ runId }) {
  const [run, setRun] = useState(null)
  const [events, setEvents] = useState([])
  const [logs, setLogs] = useState('')
  const [tab, setTab] = useState('events')
  const intervalRef = useRef(null)

  const refresh = useCallback(async () => {
    if (!runId) return
    try {
      const [rResp, eResp, lResp] = await Promise.all([
        fetch(`${apiBase}/runs/${runId}`),
        fetch(`${apiBase}/runs/${runId}/events?limit=500`),
        fetch(`${apiBase}/runs/${runId}/logs?tail=500`),
      ])
      if (rResp.ok) setRun(await rResp.json())
      if (eResp.ok) setEvents((await eResp.json()).events || [])
      if (lResp.ok) setLogs((await lResp.json()).logs || '')
    } catch (e) {
      console.warn('detail refresh failed', e)
    }
  }, [runId])

  useEffect(() => {
    if (!runId) return
    refresh()
    intervalRef.current = setInterval(refresh, 3000)
    return () => clearInterval(intervalRef.current)
  }, [runId, refresh])

  if (!runId) {
    return (
      <div className="bg-gray-800/70 border border-gray-700 rounded-lg p-8 text-center text-gray-500">
        Select a run to view details.
      </div>
    )
  }
  if (!run) {
    return (
      <div className="bg-gray-800/70 border border-gray-700 rounded-lg p-8 text-center text-gray-500">
        Loading run...
      </div>
    )
  }

  return (
    <div className="bg-gray-800/70 border border-gray-700 rounded-lg overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-700 flex items-center justify-between">
        <div className="min-w-0">
          <div className="font-medium truncate">{run.display_name || run.id}</div>
          <div className="text-xs text-gray-400">{run.id}</div>
        </div>
        <div className="text-xs text-gray-300 text-right">
          <div className="flex items-center gap-2 justify-end">
            {statusIcon(run.status)} <span className="capitalize">{run.status}</span>
          </div>
          {run.job_name && <div className="text-gray-500">job: {run.job_name}</div>}
          {run.pod_name && <div className="text-gray-500">pod: {run.pod_name}</div>}
        </div>
      </div>
      <div className="px-4 py-2 grid grid-cols-2 gap-y-1 gap-x-4 text-xs text-gray-300 border-b border-gray-700">
        <div>Model: <span className="text-gray-100">{run.llm_provider} / {run.llm_model}</span></div>
        <div>MCPs: <span className="text-gray-100">{(run.mcp_servers || []).join(', ') || '(none)'}</span></div>
        <div>Created: <span className="text-gray-100">{fmtTime(run.created_at)}</span></div>
        <div>Finished: <span className="text-gray-100">{fmtTime(run.finished_at)}</span></div>
        {run.error && (
          <div className="col-span-2 text-red-400">Error: {run.error}</div>
        )}
      </div>
      <div className="flex gap-1 px-4 py-2 border-b border-gray-700">
        <button
          onClick={() => setTab('events')}
          className={`px-3 py-1 text-xs rounded ${tab === 'events' ? 'bg-indigo-600' : 'bg-gray-700'}`}
        >
          Events
        </button>
        <button
          onClick={() => setTab('logs')}
          className={`px-3 py-1 text-xs rounded ${tab === 'logs' ? 'bg-indigo-600' : 'bg-gray-700'}`}
        >
          Pod logs
        </button>
        <button
          onClick={() => setTab('prompt')}
          className={`px-3 py-1 text-xs rounded ${tab === 'prompt' ? 'bg-indigo-600' : 'bg-gray-700'}`}
        >
          Prompt
        </button>
        <button
          onClick={refresh}
          title="Refresh now"
          className="ml-auto px-2 py-1 text-xs rounded bg-gray-700 hover:bg-gray-600 inline-flex items-center gap-1"
        >
          <RefreshCw className="w-3 h-3" /> refresh
        </button>
      </div>
      <div className="bg-black/40 font-mono text-xs h-[40vh] overflow-auto p-3 whitespace-pre-wrap break-words">
        {tab === 'events' && (
          events.length === 0
            ? <span className="text-gray-500">No events yet.</span>
            : events.map((e) => (
              <div key={e.id} className="mb-1">
                <span className="text-gray-500">{fmtTime(e.ts)}</span>{' '}
                <span className="text-indigo-300">[{e.kind}]</span>{' '}
                <span>{e.message}</span>
              </div>
            ))
        )}
        {tab === 'logs' && (
          logs ? logs : <span className="text-gray-500">No pod logs yet.</span>
        )}
        {tab === 'prompt' && (run.prompt || <span className="text-gray-500">(no prompt)</span>)}
      </div>
    </div>
  )
}

export default function AgentPortalV3() {
  const navigate = useNavigate()
  const [cap, setCap] = useState(null)
  const [servers, setServers] = useState([])
  const [models, setModels] = useState([])
  const [runs, setRuns] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState('')

  const fetchBootstrap = useCallback(async () => {
    try {
      const [capR, sR, mR] = await Promise.all([
        fetch(`${apiBase}/capabilities`),
        fetch(`${apiBase}/mcp-servers`),
        fetch(`${apiBase}/models`),
      ])
      if (capR.ok) setCap(await capR.json())
      if (sR.ok) setServers((await sR.json()).servers || [])
      if (mR.ok) setModels((await mR.json()).models || [])
    } catch (e) {
      console.warn('bootstrap failed', e)
    }
  }, [])

  const fetchRuns = useCallback(async () => {
    try {
      const r = await fetch(`${apiBase}/runs`)
      if (r.ok) {
        const json = await r.json()
        setRuns(json.runs || [])
      }
    } catch (e) {
      console.warn('runs fetch failed', e)
    }
  }, [])

  useEffect(() => {
    fetchBootstrap()
    fetchRuns()
    const id = setInterval(fetchRuns, 3000)
    return () => clearInterval(id)
  }, [fetchBootstrap, fetchRuns])

  const launchRun = async (req) => {
    setBusy(true)
    try {
      const resp = await fetch(`${apiBase}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      })
      if (resp.ok) {
        const r = await resp.json()
        if (r.dropped_mcp_servers && r.dropped_mcp_servers.length) {
          setToast(`Launched. Dropped stdio MCPs: ${r.dropped_mcp_servers.join(', ')}`)
        } else {
          setToast(`Launched run ${r.id.slice(0, 8)}`)
        }
        setSelectedId(r.id)
        await fetchRuns()
      } else {
        const t = await resp.text()
        setToast(`Launch failed: ${t}`)
      }
    } catch (e) {
      setToast(`Launch failed: ${e.message}`)
    } finally {
      setBusy(false)
      setTimeout(() => setToast(''), 4000)
    }
  }

  const cancelRun = async (id) => {
    try {
      const resp = await fetch(`${apiBase}/runs/${id}/cancel`, { method: 'POST' })
      if (resp.ok) {
        setToast(`Cancelled ${id.slice(0, 8)}`)
        fetchRuns()
      }
    } catch (e) {
      console.warn(e)
    } finally {
      setTimeout(() => setToast(''), 3000)
    }
  }

  const deleteRun = async (id) => {
    try {
      const resp = await fetch(`${apiBase}/runs/${id}`, { method: 'DELETE' })
      if (resp.ok) {
        setToast(`Deleted ${id.slice(0, 8)}`)
        if (selectedId === id) setSelectedId(null)
        fetchRuns()
      }
    } catch (e) {
      console.warn(e)
    } finally {
      setTimeout(() => setToast(''), 3000)
    }
  }

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <header className="px-6 py-3 border-b border-gray-800 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="p-1.5 rounded hover:bg-gray-800"
            title="Back to chat"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <div className="text-lg font-semibold flex items-center gap-2">
              <Terminal className="w-5 h-5 text-indigo-400" /> Agent Portal V3
            </div>
            <div className="text-xs text-gray-400">
              Launch one-shot agents as Kubernetes Jobs
            </div>
          </div>
        </div>
        <ConnInfo cap={cap} />
      </header>

      {toast && (
        <div className="mx-6 mt-3 px-3 py-2 bg-indigo-900/40 border border-indigo-600/40 text-indigo-100 rounded text-sm">
          {toast}
        </div>
      )}

      <main className="max-w-7xl mx-auto p-6 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <LaunchForm
          servers={servers}
          models={models}
          onLaunch={launchRun}
          busy={busy}
        />
        <RunsTable
          runs={runs}
          onSelect={setSelectedId}
          selectedId={selectedId}
          onCancel={cancelRun}
          onDelete={deleteRun}
        />
        <div className="lg:col-span-2">
          <RunDetail runId={selectedId} />
        </div>
      </main>
    </div>
  )
}
