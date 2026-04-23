import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, Rocket, RefreshCw, XCircle, AlertTriangle, Shield,
  Cpu, Info, Plus, Pause, Play, Copy, FolderOpen, X,
} from 'lucide-react'

/* ============================================================
 * Constants + helpers
 * ============================================================ */

const TIER_STYLES = {
  restrictive: 'bg-emerald-900/50 text-emerald-300 border-emerald-800',
  standard: 'bg-blue-900/50 text-blue-300 border-blue-800',
  permissive: 'bg-red-900/50 text-red-300 border-red-800',
}

const STATE_STYLES = {
  pending: 'bg-gray-700 text-gray-300 border-gray-600',
  authenticating: 'bg-yellow-900/50 text-yellow-300 border-yellow-800',
  launching: 'bg-indigo-900/50 text-indigo-300 border-indigo-800',
  running: 'bg-green-900/50 text-green-300 border-green-800',
  ending: 'bg-orange-900/50 text-orange-300 border-orange-800',
  ended: 'bg-gray-800 text-gray-400 border-gray-700',
  failed: 'bg-red-900/50 text-red-300 border-red-800',
  reaped: 'bg-red-900/50 text-red-300 border-red-800',
}

const TERMINAL_STATES = new Set(['ended', 'failed', 'reaped'])
const STREAM_SCROLLBACK_LINES = 10000

const DEFAULT_BUDGET = {
  wall_clock_seconds: 3600,
  tool_calls: 200,
  tokens: 200000,
  idle_timeout_seconds: 3600,
  hard_ttl_seconds: 86400,
}

function fmtTime(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleTimeString() } catch { return iso }
}
function fmtDate(iso) {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}
function shortId(id) {
  if (!id) return ''
  return id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-3)}` : id
}

// Tiny ANSI stripper. Full-color requires a lib; we keep it readable by
// dropping CSI sequences and mapping a few common resets.
// eslint-disable-next-line no-control-regex
const ANSI_CSI = /\x1b\[[0-9;?]*[ -/]*[@-~]/g
// eslint-disable-next-line no-control-regex
const ANSI_OSC = /\x1b\].*?(?:\x07|\x1b\\)/g
function stripAnsi(s) {
  return (s || '').replace(ANSI_OSC, '').replace(ANSI_CSI, '')
}

/* ============================================================
 * Shared UI atoms
 * ============================================================ */

function Badge({ children, className = '' }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${className}`}>
      {children}
    </span>
  )
}

function Notification({ kind, message, onDismiss }) {
  if (!message) return null
  const styles = kind === 'error'
    ? 'bg-red-900/60 border-red-700 text-red-100'
    : 'bg-green-900/60 border-green-700 text-green-100'
  return (
    <div className={`flex items-start gap-3 px-4 py-2 rounded-md border text-sm ${styles}`}>
      {kind === 'error'
        ? <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
        : <Rocket className="w-4 h-4 flex-shrink-0 mt-0.5" />}
      <div className="flex-1 leading-snug">{message}</div>
      <button onClick={onDismiss} className="text-gray-300 hover:text-white" aria-label="Dismiss">
        <XCircle className="w-4 h-4" />
      </button>
    </div>
  )
}

function InfoPopover({ title, body }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className="inline-flex items-center align-middle p-0.5 rounded text-gray-400 hover:text-gray-200"
        aria-label={`info about ${title}`}
      >
        <Info className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute z-50 left-0 top-full mt-1 w-72 p-3 bg-gray-800 border border-gray-600 rounded-lg shadow-xl text-xs text-gray-200">
          <div className="font-semibold mb-1">{title}</div>
          <div className="leading-relaxed whitespace-pre-wrap">{body}</div>
        </div>
      )}
    </span>
  )
}

/* ============================================================
 * Launch modal
 * ============================================================ */

function LaunchModal({ open, onClose, config, presets, rootPatterns, onLaunch, launching }) {
  const [presetId, setPresetId] = useState('')
  const [scope, setScope] = useState('')
  const [root, setRoot] = useState('')
  const [additionalPaths, setAdditionalPaths] = useState('')
  const [tier, setTier] = useState('standard')
  const [budget, setBudget] = useState(DEFAULT_BUDGET)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const preset = useMemo(
    () => presets.find(p => p.id === presetId) || null,
    [presets, presetId]
  )

  // Reset / default fields when the modal opens or when the preset changes.
  useEffect(() => {
    if (open && presets.length && !presetId) {
      const first = presets[0]
      setPresetId(first.id)
    }
  }, [open, presets, presetId])

  useEffect(() => {
    if (!preset) return
    setTier(preset.default_tier)
  }, [preset])

  if (!open) return null

  const canSubmit = !!preset && scope.trim().length > 0 && (!preset.requires_root || root.trim().length > 0) && !launching

  const permissiveAllowed = (config?.mode === 'dev') && config?.allow_permissive_tier === true

  const submit = (e) => {
    e.preventDefault()
    if (!canSubmit) return
    const additional = additionalPaths.split(',').map(s => s.trim()).filter(Boolean)
    const spec = {
      preset_id: preset.id,
      scope: scope.trim(),
      sandbox_tier: tier,
      budget,
    }
    if (preset.requires_root) {
      spec.workspace = {
        root: root.trim(),
        additional_read_paths: [],
        additional_read_write_paths: additional,
      }
    }
    onLaunch(spec)
  }

  const closeOnBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose()
  }

  const tierMeta = config?.tiers || {}

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
      onClick={closeOnBackdrop}
    >
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto bg-gray-900 border border-gray-700 rounded-lg shadow-2xl">
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 px-5 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Rocket className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-gray-100">Launch agent session</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={submit} className="p-5 space-y-4">
          <label className="block">
            <span className="text-sm text-gray-300">Preset <span className="text-red-400">*</span></span>
            <select
              value={presetId}
              onChange={e => setPresetId(e.target.value)}
              required
              className="mt-1 w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {presets.length === 0 ? (
                <option value="">No presets available for your account</option>
              ) : (
                presets.map(p => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))
              )}
            </select>
            {preset && (
              <div className="mt-1 text-xs text-gray-500">{preset.description}</div>
            )}
          </label>

          <label className="block">
            <span className="text-sm text-gray-300">Scope <span className="text-red-400">*</span></span>
            <textarea
              value={scope}
              onChange={e => setScope(e.target.value)}
              rows={3}
              maxLength={4000}
              required
              placeholder="What should this agent do?"
              className="mt-1 w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
            />
            <span className="text-xs text-gray-500">{scope.length}/4000</span>
          </label>

          {preset?.requires_root && (
            <div>
              <label className="block">
                <span className="text-sm text-gray-300 flex items-center gap-1.5">
                  <FolderOpen className="w-3.5 h-3.5" /> Root folder <span className="text-red-400">*</span>
                  <InfoPopover
                    title="Root folder"
                    body={rootPatterns.length
                      ? `Your allowed root globs:\n${rootPatterns.join('\n')}`
                      : 'Your account does not have any allowed workspace roots configured.'}
                  />
                </span>
                <input
                  value={root}
                  onChange={e => setRoot(e.target.value)}
                  required
                  placeholder={rootPatterns[0] || '/tmp/my-project'}
                  className="mt-1 w-full font-mono bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
              <label className="block mt-3">
                <span className="text-xs text-gray-400">Additional read-write paths</span>
                <input
                  value={additionalPaths}
                  onChange={e => setAdditionalPaths(e.target.value)}
                  placeholder="(optional, comma-separated)"
                  className="mt-1 w-full font-mono bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-xs text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
            </div>
          )}

          <div>
            <div className="text-sm text-gray-300 flex items-center gap-1.5 mb-1">
              <Shield className="w-3.5 h-3.5" /> Sandbox tier
            </div>
            <div className="flex flex-col gap-2">
              {['restrictive', 'standard', 'permissive'].map(t => {
                const info = tierMeta[t] || {}
                const allowedByPreset = preset ? preset.allowed_tiers.includes(t) : true
                const allowedByMode = t !== 'permissive' || permissiveAllowed
                const disabled = !allowedByPreset || !allowedByMode
                return (
                  <label
                    key={t}
                    className={`flex items-start gap-2 p-2 rounded border cursor-pointer ${
                      tier === t
                        ? 'border-blue-500 bg-gray-800'
                        : 'border-gray-700 bg-gray-800/50 hover:bg-gray-800'
                    } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <input
                      type="radio"
                      name="tier"
                      value={t}
                      checked={tier === t}
                      disabled={disabled}
                      onChange={() => setTier(t)}
                      className="mt-0.5"
                    />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <Badge className={TIER_STYLES[t]}>{t}</Badge>
                        {!allowedByPreset && <span className="text-xs text-gray-500">(not allowed by preset)</span>}
                        {!allowedByMode && (
                          <span className="text-xs text-gray-500">
                            {config?.mode === 'prod'
                              ? '(disabled in prod mode)'
                              : '(AGENT_PORTAL_ALLOW_PERMISSIVE_TIER=false)'}
                          </span>
                        )}
                        <InfoPopover
                          title={`${t} tier`}
                          body={[
                            info.summary,
                            info.network && `Network: ${info.network}`,
                            info.filesystem && `Filesystem: ${info.filesystem}`,
                            info.env && `Env: ${info.env}`,
                          ].filter(Boolean).join('\n\n')}
                        />
                      </div>
                      {info.summary && (
                        <div className="text-xs text-gray-400 mt-1">{info.summary}</div>
                      )}
                    </div>
                  </label>
                )
              })}
            </div>
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced(v => !v)}
              className="text-sm text-gray-400 hover:text-gray-200"
            >
              {showAdvanced ? '▾' : '▸'} Budget (advanced)
            </button>
            {showAdvanced && (
              <div className="grid grid-cols-2 gap-2 mt-2 p-3 bg-gray-800/50 border border-gray-700 rounded">
                {Object.entries(DEFAULT_BUDGET).map(([key]) => (
                  <label key={key} className="block">
                    <span className="text-xs text-gray-400">{key}</span>
                    <input
                      type="number"
                      min="0"
                      value={budget[key]}
                      onChange={e => {
                        const n = Number(e.target.value)
                        setBudget(b => ({ ...b, [key]: Number.isFinite(n) && n >= 0 ? n : b[key] }))
                      }}
                      className="mt-1 w-full bg-gray-900 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  </label>
                ))}
              </div>
            )}
          </div>

          <div className="flex gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 py-2 rounded-md bg-gray-700 hover:bg-gray-600 text-gray-100"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-md font-medium transition-colors ${
                canSubmit ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-gray-700 text-gray-500 cursor-not-allowed'
              }`}
            >
              <Rocket className="w-4 h-4" />
              {launching ? 'Launching…' : 'Launch'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ============================================================
 * Session list (sidebar)
 * ============================================================ */

function SessionListItem({ session, selected, onSelect }) {
  const isTerminal = TERMINAL_STATES.has(session.state)
  return (
    <button
      type="button"
      onClick={() => onSelect(session.id)}
      className={`w-full text-left px-3 py-2 rounded-md border transition-colors ${
        selected
          ? 'bg-gray-700 border-blue-500'
          : 'bg-gray-800/70 border-gray-700 hover:bg-gray-700/80 hover:border-gray-600'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <code className="text-xs font-mono text-gray-200">{shortId(session.id)}</code>
        <Badge className={STATE_STYLES[session.state]}>{session.state}</Badge>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Badge className={TIER_STYLES[session.sandbox_tier]}>{session.sandbox_tier}</Badge>
        <span className="text-[11px] text-gray-500">{isTerminal ? fmtDate(session.updated_at) : fmtTime(session.created_at)}</span>
      </div>
      {session.preset_id && (
        <div className="text-[11px] text-gray-500 mt-1 truncate">preset: {session.preset_id}</div>
      )}
    </button>
  )
}

function Sidebar({ sessions, selectedId, onSelect, onLaunchClick, onRefresh }) {
  const [showRecent, setShowRecent] = useState(false)
  const active = sessions.filter(s => !TERMINAL_STATES.has(s.state))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))
  const recent = sessions.filter(s => TERMINAL_STATES.has(s.state))
    .sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''))

  return (
    <div className="w-64 flex-shrink-0 flex flex-col bg-gray-900 border-r border-gray-800">
      <div className="p-3 border-b border-gray-800 flex gap-2">
        <button
          onClick={onLaunchClick}
          className="flex-1 inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium"
        >
          <Plus className="w-4 h-4" /> New
        </button>
        <button
          onClick={onRefresh}
          className="p-2 rounded-md bg-gray-800 hover:bg-gray-700 text-gray-300"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-2 px-1">Active ({active.length})</div>
          {active.length === 0 ? (
            <div className="text-xs text-gray-600 px-1">No active sessions.</div>
          ) : (
            <div className="space-y-2">
              {active.map(s => (
                <SessionListItem key={s.id} session={s} selected={s.id === selectedId} onSelect={onSelect} />
              ))}
            </div>
          )}
        </div>

        <div>
          <button
            type="button"
            onClick={() => setShowRecent(v => !v)}
            className="text-[11px] uppercase tracking-wide text-gray-500 hover:text-gray-300 px-1"
          >
            {showRecent ? '▾' : '▸'} Recent ({recent.length})
          </button>
          {showRecent && recent.length > 0 && (
            <div className="space-y-2 mt-2">
              {recent.map(s => (
                <SessionListItem key={s.id} session={s} selected={s.id === selectedId} onSelect={onSelect} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ============================================================
 * Stream view
 * ============================================================ */

function StreamView({ sessionId, terminal }) {
  const [lines, setLines] = useState([])
  const [status, setStatus] = useState('connecting')
  const [autoScroll, setAutoScroll] = useState(true)
  const lastSeqRef = useRef(0)
  const containerRef = useRef(null)

  // Reset lines + last seq whenever the selected session changes.
  useEffect(() => {
    setLines([])
    lastSeqRef.current = 0
    setStatus('connecting')
  }, [sessionId])

  useEffect(() => {
    if (!sessionId) return
    let es = null
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      const url = `/api/agent-portal/sessions/${encodeURIComponent(sessionId)}/stream?since_seq=${lastSeqRef.current}`
      es = new EventSource(url)
      es.addEventListener('frame', (ev) => {
        try {
          const frame = JSON.parse(ev.data)
          if (typeof frame.seq === 'number') lastSeqRef.current = frame.seq
          setStatus('connected')
          setLines(prev => {
            const next = prev.concat([{
              seq: frame.seq,
              ts: frame.ts,
              stream: frame.stream,
              text: frame.text ?? (frame.payload ? JSON.stringify(frame.payload) : ''),
              event: frame.event,
            }])
            if (next.length > STREAM_SCROLLBACK_LINES) {
              return next.slice(next.length - STREAM_SCROLLBACK_LINES)
            }
            return next
          })
        } catch {
          /* skip */
        }
      })
      es.addEventListener('keepalive', () => { /* noop */ })
      es.onerror = () => {
        setStatus('reconnecting')
        es?.close()
        if (!cancelled && !terminal) {
          setTimeout(connect, 1000)
        }
      }
    }
    connect()
    return () => {
      cancelled = true
      es?.close()
    }
  }, [sessionId, terminal])

  // Auto-scroll to bottom when new lines arrive.
  useEffect(() => {
    if (!autoScroll) return
    const el = containerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, autoScroll])

  const copyAll = () => {
    const text = lines.map(l => {
      const prefix = l.stream === 'lifecycle' ? `[${l.stream}]` : ''
      const body = l.stream === 'lifecycle' ? (l.event || l.text) : (l.text || '')
      return `${prefix}${body}`
    }).join('')
    navigator.clipboard?.writeText(text).catch(() => {})
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800 bg-gray-900/60 text-xs">
        <span className="text-gray-500">
          {terminal ? '● ended' : status === 'connected' ? '● connected' : `○ ${status}`}
        </span>
        <span className="text-gray-500">· {lines.length} lines</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => setAutoScroll(v => !v)}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            title={autoScroll ? 'Pause auto-scroll' : 'Resume auto-scroll'}
          >
            {autoScroll ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
            {autoScroll ? 'Pause' : 'Resume'}
          </button>
          <button
            type="button"
            onClick={copyAll}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            title="Copy full log"
          >
            <Copy className="w-3 h-3" /> Copy
          </button>
          <button
            type="button"
            onClick={() => setLines([])}
            className="inline-flex items-center gap-1 px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            title="Clear view"
          >
            Clear
          </button>
        </div>
      </div>
      <div
        ref={containerRef}
        className="flex-1 overflow-auto bg-black text-gray-100 font-mono text-[12px] leading-[1.35] p-3 whitespace-pre-wrap"
      >
        {lines.length === 0 ? (
          <div className="text-gray-600">Waiting for output…</div>
        ) : (
          lines.map((l, i) => {
            if (l.stream === 'lifecycle') {
              return (
                <div key={i} className="text-yellow-400">
                  [{l.event || 'lifecycle'}]{'\n'}
                </div>
              )
            }
            if (l.stream === 'policy') {
              return (
                <div key={i} className="text-cyan-400">
                  [policy]{'\n'}
                </div>
              )
            }
            const cls = l.stream === 'stderr' ? 'text-red-300' : 'text-gray-100'
            return <span key={i} className={cls}>{stripAnsi(l.text)}</span>
          })
        )}
      </div>
    </div>
  )
}

/* ============================================================
 * Main session detail (tabs: Stream / Metadata)
 * ============================================================ */

function SessionDetail({ session, onCancel, busy }) {
  const [tab, setTab] = useState('stream')

  if (!session) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
        Select or launch a session to see live output.
      </div>
    )
  }

  const terminal = TERMINAL_STATES.has(session.state)

  return (
    <div className="flex flex-col flex-1 min-w-0 min-h-0">
      <div className="flex items-start justify-between gap-3 p-3 border-b border-gray-800">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Badge className={STATE_STYLES[session.state]}>{session.state}</Badge>
            <Badge className={TIER_STYLES[session.sandbox_tier]}>{session.sandbox_tier}</Badge>
            {session.preset_id && (
              <span className="text-xs text-gray-400">preset: <code className="text-gray-300">{session.preset_id}</code></span>
            )}
          </div>
          <code className="block text-xs font-mono text-gray-400 break-all">{session.id}</code>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {!terminal && (
            <button
              onClick={() => onCancel(session.id)}
              disabled={busy}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md bg-red-700 hover:bg-red-600 text-white text-sm font-medium disabled:opacity-50"
            >
              <XCircle className="w-4 h-4" /> Cancel
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1 px-3 border-b border-gray-800 bg-gray-900/40">
        {['stream', 'metadata'].map(k => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm border-b-2 ${
              tab === k ? 'text-gray-100 border-blue-500' : 'text-gray-400 border-transparent hover:text-gray-200'
            }`}
          >
            {k === 'stream' ? 'Stream' : 'Metadata'}
          </button>
        ))}
      </div>

      {tab === 'stream' && (
        <StreamView sessionId={session.id} terminal={terminal} />
      )}

      {tab === 'metadata' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-3 text-sm">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">State</div>
              <div className="mt-1"><Badge className={STATE_STYLES[session.state]}>{session.state}</Badge></div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Sandbox</div>
              <div className="mt-1"><Badge className={TIER_STYLES[session.sandbox_tier]}>{session.sandbox_tier}</Badge></div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Created</div>
              <div className="mt-1 text-gray-300">{fmtDate(session.created_at)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Updated</div>
              <div className="mt-1 text-gray-300">{fmtDate(session.updated_at)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Preset</div>
              <div className="mt-1 text-gray-300">{session.preset_id || '—'}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-gray-500">Termination reason</div>
              <div className="mt-1 text-gray-300">{session.termination_reason || '—'}</div>
            </div>
          </div>
          <div className="pt-2 border-t border-gray-800 text-xs text-gray-500">
            Session ID: <code className="text-gray-300">{session.id}</code>
          </div>
        </div>
      )}
    </div>
  )
}

/* ============================================================
 * Top-level
 * ============================================================ */

function AgentPortal() {
  const navigate = useNavigate()
  const [config, setConfig] = useState(null)
  const [presets, setPresets] = useState([])
  const [rootPatterns, setRootPatterns] = useState([])
  const [sessions, setSessions] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [busyCancel, setBusyCancel] = useState(new Set())
  const [loadError, setLoadError] = useState('')
  const [notification, setNotification] = useState(null)

  const notify = useCallback((kind, message) => {
    setNotification({ kind, message })
    const t = setTimeout(() => setNotification(null), 6000)
    return () => clearTimeout(t)
  }, [])

  const fetchConfig = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-portal/config')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setConfig(await res.json())
    } catch (err) {
      setLoadError(`Failed to load config: ${err.message}`)
    }
  }, [])

  const fetchPresets = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-portal/presets')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setPresets(await res.json())
    } catch (err) {
      setLoadError(`Failed to load presets: ${err.message}`)
    }
  }, [])

  const fetchRoots = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-portal/workspace-roots')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRootPatterns(data.patterns || [])
    } catch {
      setRootPatterns([])
    }
  }, [])

  const fetchSessions = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-portal/sessions')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setSessions(Array.isArray(data) ? data : [])
    } catch (err) {
      setLoadError(`Failed to load sessions: ${err.message}`)
    }
  }, [])

  useEffect(() => { fetchConfig() }, [fetchConfig])
  useEffect(() => { fetchPresets() }, [fetchPresets])
  useEffect(() => { fetchRoots() }, [fetchRoots])
  useEffect(() => { fetchSessions() }, [fetchSessions])

  useEffect(() => {
    const id = setInterval(fetchSessions, 5000)
    return () => clearInterval(id)
  }, [fetchSessions])

  const selectedSession = useMemo(
    () => sessions.find(s => s.id === selectedId) || null,
    [sessions, selectedId]
  )

  const onLaunch = useCallback(async (spec) => {
    setLaunching(true)
    try {
      const res = await fetch('/api/agent-portal/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(spec),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = body?.detail || body?.message || `HTTP ${res.status}`
        throw new Error(msg)
      }
      notify('success', `Session launched: ${shortId(body.id || '')}`)
      setSelectedId(body.id || null)
      setModalOpen(false)
      await fetchSessions()
    } catch (err) {
      notify('error', `Launch failed: ${err.message}`)
    } finally {
      setLaunching(false)
    }
  }, [fetchSessions, notify])

  const onCancel = useCallback(async (id) => {
    setBusyCancel(prev => new Set(prev).add(id))
    try {
      const res = await fetch(`/api/agent-portal/sessions/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        const msg = body?.detail || body?.message || `HTTP ${res.status}`
        throw new Error(msg)
      }
      notify('success', `Session ${shortId(id)} cancelled`)
      await fetchSessions()
    } catch (err) {
      notify('error', `Cancel failed: ${err.message}`)
    } finally {
      setBusyCancel(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }, [fetchSessions, notify])

  if (config && config.enabled === false) {
    return (
      <div className="flex flex-col h-screen bg-gray-900 text-gray-200">
        <div className="p-4 border-b border-gray-800 flex items-center gap-3">
          <button onClick={() => navigate('/')} className="p-2 rounded hover:bg-gray-800" title="Back">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <h1 className="text-lg font-semibold">Agent Portal</h1>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center space-y-2">
            <Shield className="w-10 h-10 mx-auto text-gray-500" />
            <div className="text-gray-300">Agent Portal is disabled on this server.</div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-gray-200">
      <div className="p-3 border-b border-gray-800 flex items-center gap-3">
        <button onClick={() => navigate('/')} className="p-2 rounded hover:bg-gray-800 text-gray-300" title="Back to chat">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <Cpu className="w-5 h-5 text-blue-400" />
        <h1 className="text-lg font-semibold">Agent Portal</h1>
        <span className="text-xs px-2 py-0.5 rounded bg-yellow-900/50 border border-yellow-800 text-yellow-300">
          experimental
        </span>
        {config?.mode && (
          <Badge className={config.mode === 'prod' ? 'bg-red-900/50 text-red-300 border-red-800' : 'bg-gray-800 text-gray-300 border-gray-700'}>
            mode: {config.mode}
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-gray-500">{sessions.length} sessions total</span>
        </div>
      </div>

      {(loadError || notification) && (
        <div className="p-2 space-y-2">
          {loadError && <Notification kind="error" message={loadError} onDismiss={() => setLoadError('')} />}
          {notification && (
            <Notification
              kind={notification.kind}
              message={notification.message}
              onDismiss={() => setNotification(null)}
            />
          )}
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <Sidebar
          sessions={sessions}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onLaunchClick={() => setModalOpen(true)}
          onRefresh={fetchSessions}
        />
        <SessionDetail
          session={selectedSession}
          onCancel={onCancel}
          busy={selectedSession ? busyCancel.has(selectedSession.id) : false}
        />
      </div>

      <LaunchModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        config={config}
        presets={presets}
        rootPatterns={rootPatterns}
        onLaunch={onLaunch}
        launching={launching}
      />
    </div>
  )
}

export default AgentPortal
