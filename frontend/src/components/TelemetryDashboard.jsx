import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Activity, BarChart3, Wrench, Database, Search,
  RefreshCw, AlertTriangle, ChevronRight
} from 'lucide-react'

const RANGES = [
  { value: '1h', label: 'Last hour' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
]

const TABS = [
  { id: 'overview', label: 'Overview', Icon: BarChart3 },
  { id: 'tools', label: 'Tool health', Icon: Wrench },
  { id: 'llm', label: 'LLM performance', Icon: Activity },
  { id: 'rag', label: 'RAG effectiveness', Icon: Database },
  { id: 'session', label: 'Session drill-down', Icon: Search },
]

function fmtNumber(n) {
  if (n == null || Number.isNaN(n)) return '—'
  if (typeof n !== 'number') return String(n)
  if (n >= 1000) return n.toLocaleString()
  if (Number.isInteger(n)) return String(n)
  return n.toFixed(2)
}

function fmtMs(n) {
  if (n == null || Number.isNaN(n)) return '—'
  if (n >= 1000) return `${(n / 1000).toFixed(2)} s`
  return `${Math.round(n)} ms`
}

function fmtPct(n) {
  if (n == null || Number.isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtRelTime(ns) {
  if (!ns) return '—'
  const ms = ns / 1e6
  const d = new Date(ms)
  return d.toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

function Stat({ label, value, hint }) {
  return (
    <div className="bg-gray-700/40 rounded-lg p-4">
      <div className="text-xs uppercase tracking-wide text-gray-400 mb-1">{label}</div>
      <div className="text-2xl font-semibold text-gray-100">{value}</div>
      {hint && <div className="text-xs text-gray-500 mt-1">{hint}</div>}
    </div>
  )
}

function ErrorBanner({ message }) {
  if (!message) return null
  return (
    <div className="flex items-start gap-2 bg-red-900/30 border border-red-600/40 text-red-200 rounded-lg p-3 mb-4">
      <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
      <div className="text-sm">{message}</div>
    </div>
  )
}

async function apiGet(path) {
  const resp = await fetch(path)
  if (resp.status === 403) throw new Error('Admin access required')
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json()
}

// --------------------------------------------------------------------------
// Overview
// --------------------------------------------------------------------------

function OverviewView({ range }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const d = await apiGet(`/admin/telemetry/overview?range=${encodeURIComponent(range)}`)
      setData(d)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <ErrorBanner message={err} />
      {loading && <div className="text-gray-400 text-sm mb-3">Loading…</div>}
      {data && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Stat label="Chat sessions" value={fmtNumber(data.sessions)} />
          <Stat label="Chat turns" value={fmtNumber(data.turns)}
            hint={data.sessions ? `${(data.turns / data.sessions).toFixed(1)} turns/session` : undefined} />
          <Stat label="Tool calls" value={fmtNumber(data.tool_calls)}
            hint={`success rate ${fmtPct(data.tool_success_rate)}`} />
          <Stat label="LLM calls" value={fmtNumber(data.llm_calls)}
            hint={`retries: ${fmtNumber(data.llm_retries_total)}`} />
          <Stat label="RAG queries" value={fmtNumber(data.rag_queries)} />
          <Stat label="LLM latency p50" value={fmtMs(data.llm_latency_p50_ms)} />
          <Stat label="LLM latency p95" value={fmtMs(data.llm_latency_p95_ms)} />
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// Tool health
// --------------------------------------------------------------------------

function ToolsView({ range }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)
  const [expandedTool, setExpandedTool] = useState(null)
  const [failures, setFailures] = useState(null)
  const failuresReqIdRef = React.useRef(0)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const d = await apiGet(`/admin/telemetry/tools?range=${encodeURIComponent(range)}`)
      setData(d)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { load() }, [load])

  const openFailures = async (tool) => {
    if (expandedTool === tool) {
      setExpandedTool(null)
      setFailures(null)
      failuresReqIdRef.current += 1
      return
    }
    const reqId = ++failuresReqIdRef.current
    setExpandedTool(tool)
    setFailures(null)
    try {
      const d = await apiGet(`/admin/telemetry/tools/${encodeURIComponent(tool)}/failures?range=${encodeURIComponent(range)}`)
      if (reqId !== failuresReqIdRef.current) return
      setFailures(d.failures)
    } catch (e) {
      if (reqId !== failuresReqIdRef.current) return
      setFailures([])
      setErr(e.message)
    }
  }

  return (
    <div>
      <ErrorBanner message={err} />
      {loading && <div className="text-gray-400 text-sm mb-3">Loading…</div>}
      {data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-2">Tool</th>
                <th className="text-right p-2">Calls</th>
                <th className="text-right p-2">Success rate</th>
                <th className="text-right p-2">Failures</th>
                <th className="text-right p-2">p50 duration</th>
                <th className="text-right p-2">p95 duration</th>
                <th className="text-left p-2">Last failure</th>
              </tr>
            </thead>
            <tbody>
              {data.tools.length === 0 && (
                <tr><td className="p-3 text-gray-500" colSpan={7}>No tool calls in range.</td></tr>
              )}
              {data.tools.map((t) => {
                const isOpen = expandedTool === t.tool_name
                return (
                  <React.Fragment key={t.tool_name}>
                    <tr
                      className="border-b border-gray-800 hover:bg-gray-700/30 cursor-pointer"
                      onClick={() => openFailures(t.tool_name)}
                    >
                      <td className="p-2 font-mono flex items-center gap-1">
                        <ChevronRight className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-90' : ''}`} />
                        {t.tool_name}
                      </td>
                      <td className="p-2 text-right">{fmtNumber(t.call_count)}</td>
                      <td className={`p-2 text-right ${t.success_rate != null && t.success_rate < 0.9 ? 'text-yellow-300' : ''}`}>
                        {fmtPct(t.success_rate)}
                      </td>
                      <td className="p-2 text-right">{fmtNumber(t.failure_count)}</td>
                      <td className="p-2 text-right">{fmtMs(t.duration_p50_ms)}</td>
                      <td className="p-2 text-right">{fmtMs(t.duration_p95_ms)}</td>
                      <td className="p-2 text-gray-400">
                        {t.last_failure_start_ns
                          ? `${fmtRelTime(t.last_failure_start_ns)}${t.last_failure_error_type ? ` · ${t.last_failure_error_type}` : ''}`
                          : '—'}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-gray-900/40">
                        <td colSpan={7} className="p-3">
                          {failures == null && <div className="text-gray-500">Loading failures…</div>}
                          {failures != null && failures.length === 0 && <div className="text-gray-500">No failures in range.</div>}
                          {failures != null && failures.length > 0 && (
                            <ul className="space-y-1 text-xs text-gray-300">
                              {failures.map((f) => (
                                <li key={f.span_id} className="font-mono">
                                  <span className="text-gray-500">{fmtRelTime(f.start_time_ns)}</span>
                                  {' · '}
                                  <span className="text-red-300">{f.error_type || 'error'}</span>
                                  {f.duration_ms != null && <> · {fmtMs(f.duration_ms)}</>}
                                  {f.error_message && (
                                    <div className="text-gray-400 pl-4 whitespace-pre-wrap break-words">{f.error_message}</div>
                                  )}
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// LLM performance
// --------------------------------------------------------------------------

function LLMView({ range }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const d = await apiGet(`/admin/telemetry/llm?range=${encodeURIComponent(range)}`)
      setData(d)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <ErrorBanner message={err} />
      {loading && <div className="text-gray-400 text-sm mb-3">Loading…</div>}
      {data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-2">Model</th>
                <th className="text-right p-2">Calls</th>
                <th className="text-right p-2">p50</th>
                <th className="text-right p-2">p95</th>
                <th className="text-right p-2">p99</th>
                <th className="text-right p-2">Input tokens</th>
                <th className="text-right p-2">Output tokens</th>
                <th className="text-right p-2">Retry rate</th>
                <th className="text-right p-2">Errors</th>
              </tr>
            </thead>
            <tbody>
              {data.models.length === 0 && (
                <tr><td colSpan={9} className="p-3 text-gray-500">No LLM calls in range.</td></tr>
              )}
              {data.models.map((m) => (
                <tr key={m.model} className="border-b border-gray-800 hover:bg-gray-700/30">
                  <td className="p-2 font-mono">{m.model}</td>
                  <td className="p-2 text-right">{fmtNumber(m.call_count)}</td>
                  <td className="p-2 text-right">{fmtMs(m.latency_p50_ms)}</td>
                  <td className="p-2 text-right">{fmtMs(m.latency_p95_ms)}</td>
                  <td className="p-2 text-right">{fmtMs(m.latency_p99_ms)}</td>
                  <td className="p-2 text-right">{fmtNumber(m.input_tokens_total)}</td>
                  <td className="p-2 text-right">{fmtNumber(m.output_tokens_total)}</td>
                  <td className={`p-2 text-right ${m.retry_rate != null && m.retry_rate > 0.1 ? 'text-yellow-300' : ''}`}>
                    {fmtPct(m.retry_rate)}
                  </td>
                  <td className={`p-2 text-right ${m.error_count > 0 ? 'text-red-300' : ''}`}>
                    {fmtNumber(m.error_count)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// RAG effectiveness
// --------------------------------------------------------------------------

function RagView({ range }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setErr(null)
    try {
      const d = await apiGet(`/admin/telemetry/rag?range=${encodeURIComponent(range)}`)
      setData(d)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }, [range])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <ErrorBanner message={err} />
      {loading && <div className="text-gray-400 text-sm mb-3">Loading…</div>}
      {data && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase text-gray-400 border-b border-gray-700">
              <tr>
                <th className="text-left p-2">Data source</th>
                <th className="text-right p-2">Queries</th>
                <th className="text-right p-2">Docs retrieved</th>
                <th className="text-right p-2">Docs used</th>
                <th className="text-right p-2">Retrieved→used ratio</th>
                <th className="text-right p-2">Top-score p50</th>
                <th className="text-right p-2">Top-score p95</th>
                <th className="text-right p-2">Top-score max</th>
              </tr>
            </thead>
            <tbody>
              {data.sources.length === 0 && (
                <tr><td colSpan={8} className="p-3 text-gray-500">No RAG queries in range.</td></tr>
              )}
              {data.sources.map((s) => (
                <tr key={s.data_source} className="border-b border-gray-800 hover:bg-gray-700/30">
                  <td className="p-2 font-mono">{s.data_source}</td>
                  <td className="p-2 text-right">{fmtNumber(s.query_count)}</td>
                  <td className="p-2 text-right">{fmtNumber(s.docs_retrieved_total)}</td>
                  <td className="p-2 text-right">{fmtNumber(s.docs_used_total)}</td>
                  <td className="p-2 text-right">{fmtPct(s.retrieval_to_use_ratio)}</td>
                  <td className="p-2 text-right">{fmtNumber(s.top_score_p50)}</td>
                  <td className="p-2 text-right">{fmtNumber(s.top_score_p95)}</td>
                  <td className="p-2 text-right">{fmtNumber(s.top_score_max)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// Session drill-down
// --------------------------------------------------------------------------

function SessionView({ range }) {
  const [mode, setMode] = useState('session_id')
  const [value, setValue] = useState('')
  const [turns, setTurns] = useState([])
  const [selectedTurn, setSelectedTurn] = useState(null)
  const [err, setErr] = useState(null)

  const search = async () => {
    setErr(null)
    setSelectedTurn(null)
    if (!value.trim()) {
      setErr('Enter a session_id or turn_id')
      return
    }
    try {
      const d = await apiGet(
        `/admin/telemetry/sessions/search?${mode}=${encodeURIComponent(value.trim())}&range=${encodeURIComponent(range)}`,
      )
      setTurns(d.turns)
    } catch (e) {
      setErr(e.message)
    }
  }

  const loadTurn = async (turnId) => {
    setErr(null)
    try {
      const d = await apiGet(`/admin/telemetry/turn/${encodeURIComponent(turnId)}`)
      setSelectedTurn(d)
    } catch (e) {
      setErr(e.message)
    }
  }

  return (
    <div>
      <ErrorBanner message={err} />
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value)}
          className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm"
        >
          <option value="session_id">session_id</option>
          <option value="turn_id">turn_id</option>
        </select>
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') search() }}
          placeholder="UUID or identifier"
          className="flex-1 min-w-[200px] px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm font-mono"
        />
        <button
          onClick={search}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-sm"
        >
          Search
        </button>
      </div>

      {turns.length > 0 && (
        <div className="mb-4">
          <div className="text-xs uppercase text-gray-400 mb-2">
            {turns.length} matching turn{turns.length === 1 ? '' : 's'}
          </div>
          <ul className="space-y-1 text-sm">
            {turns.map((t) => (
              <li key={t.span_id}>
                <button
                  onClick={() => loadTurn(t.turn_id)}
                  className="w-full text-left p-2 bg-gray-800 hover:bg-gray-700 rounded font-mono text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-gray-300">{t.turn_id}</span>
                    <span className="text-gray-500">{fmtRelTime(t.start_time_ns)}</span>
                  </div>
                  <div className="text-gray-500 mt-1">
                    model={t.model || '—'} · session={t.session_id || '—'}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selectedTurn && <WaterfallView turn={selectedTurn} />}
    </div>
  )
}

function WaterfallView({ turn }) {
  const rootDur = turn.root_duration_ns || 1
  return (
    <div className="bg-gray-900/50 rounded-lg p-4">
      <div className="text-xs text-gray-400 mb-3 font-mono">
        trace_id={turn.trace_id} · {turn.span_count} spans · {fmtMs(turn.root_duration_ns / 1e6)}
      </div>
      <div className="space-y-1">
        {turn.waterfall.map((span) => {
          const relStart = span.relative_start_ns || 0
          const dur = span.duration_ns || 0
          const leftPct = Math.min(100, (relStart / rootDur) * 100)
          const widthPct = Math.max(0.5, Math.min(100 - leftPct, (dur / rootDur) * 100))
          const isError = span.status === 'ERROR'
          return (
            <div key={span.span_id} className="text-xs">
              <div className="flex items-center gap-2">
                <span
                  className="font-mono text-gray-300 truncate"
                  style={{ paddingLeft: `${span.depth * 16}px`, width: '34%' }}
                  title={span.name}
                >
                  {span.name}
                </span>
                <div className="flex-1 relative h-4 bg-gray-800 rounded">
                  <div
                    className={`absolute top-0 h-full rounded ${isError ? 'bg-red-500/70' : 'bg-blue-500/70'}`}
                    style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                  />
                </div>
                <span className="w-20 text-right text-gray-400 font-mono">{fmtMs(span.duration_ms)}</span>
              </div>
              <details className="ml-10 text-gray-500">
                <summary className="cursor-pointer text-gray-600 hover:text-gray-400">attributes</summary>
                <pre className="text-xs overflow-x-auto bg-gray-900 p-2 rounded mt-1 font-mono">
                  {JSON.stringify(span.attributes, null, 2)}
                </pre>
              </details>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// Page shell
// --------------------------------------------------------------------------

const TelemetryDashboard = () => {
  const navigate = useNavigate()
  const [range, setRange] = useState('24h')
  const [tab, setTab] = useState('overview')
  const [status, setStatus] = useState(null)
  const [authError, setAuthError] = useState(null)
  const [refreshTick, setRefreshTick] = useState(0)

  const loadStatus = useCallback(async () => {
    try {
      const d = await apiGet('/admin/telemetry/status')
      setStatus(d)
      setAuthError(null)
    } catch (e) {
      if (e.message.includes('Admin')) setAuthError(e.message)
      setStatus(null)
    }
  }, [])

  useEffect(() => { loadStatus() }, [loadStatus, refreshTick])

  if (authError) {
    return (
      <div className="min-h-screen bg-gray-900 text-gray-200 flex items-center justify-center p-6">
        <div className="max-w-md text-center">
          <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-red-400" />
          <h1 className="text-xl font-semibold mb-2">Admin access required</h1>
          <p className="text-gray-400 mb-4">You need admin privileges to view telemetry.</p>
          <button
            onClick={() => navigate('/')}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg"
          >
            Back to chat
          </button>
        </div>
      </div>
    )
  }

  const CurrentView = {
    overview: <OverviewView key={`o-${range}-${refreshTick}`} range={range} />,
    tools: <ToolsView key={`t-${range}-${refreshTick}`} range={range} />,
    llm: <LLMView key={`l-${range}-${refreshTick}`} range={range} />,
    rag: <RagView key={`r-${range}-${refreshTick}`} range={range} />,
    session: <SessionView key={`s-${range}-${refreshTick}`} range={range} />,
  }[tab]

  return (
    <div className="min-h-screen bg-gray-900 text-gray-200 overflow-y-auto">
      <div className="w-full max-w-7xl mx-auto p-6">
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-2">
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Activity className="w-6 h-6 text-emerald-400" /> Telemetry
            </h1>
            <button
              onClick={() => navigate('/admin')}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg"
            >
              <ArrowLeft className="w-4 h-4" />
              Admin dashboard
            </button>
          </div>
          <p className="text-gray-400 text-sm">
            Read-only view of OpenTelemetry spans. No raw prompts, tool outputs, or RAG document text are rendered.
          </p>
          {status && status.backend && (
            <p className="text-gray-500 text-xs mt-2 font-mono">
              backend={status.backend}
              {status.path && <> · path={status.path}</>}
              {status.size_bytes != null && <> · {fmtNumber(status.size_bytes)} bytes</>}
            </p>
          )}
        </div>

        <div className="bg-gray-800 rounded-lg p-4 mb-6 flex flex-wrap items-center gap-2">
          <select
            value={range}
            onChange={(e) => setRange(e.target.value)}
            className="px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-sm"
          >
            {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
          <div className="flex flex-wrap gap-1 ml-auto">
            {TABS.map((t) => {
              const TabIcon = t.Icon
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-1 px-3 py-2 rounded-lg text-sm transition-colors ${
                    tab === t.id ? 'bg-emerald-600 text-white' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                  }`}
                >
                  <TabIcon className="w-4 h-4" />
                  {t.label}
                </button>
              )
            })}
          </div>
          <button
            onClick={() => setRefreshTick((x) => x + 1)}
            className="p-2 bg-gray-700 hover:bg-gray-600 rounded-lg"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        <div className="bg-gray-800 rounded-lg p-6">
          {CurrentView}
        </div>
      </div>
    </div>
  )
}

export default TelemetryDashboard
