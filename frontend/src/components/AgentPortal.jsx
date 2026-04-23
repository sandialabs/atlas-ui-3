import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, Square, RefreshCw, Terminal, Shield, History, X } from 'lucide-react'

const LAUNCH_HISTORY_KEY = 'atlas.agentPortal.launchHistory.v1'
const LAUNCH_HISTORY_MAX = 15

function loadLaunchHistory() {
  try {
    const raw = localStorage.getItem(LAUNCH_HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((e) => e && typeof e.command === 'string').slice(0, LAUNCH_HISTORY_MAX)
  } catch {
    return []
  }
}

function saveLaunchHistory(entries) {
  try {
    localStorage.setItem(LAUNCH_HISTORY_KEY, JSON.stringify(entries.slice(0, LAUNCH_HISTORY_MAX)))
  } catch {
    // localStorage may be disabled; ignore
  }
}

function makeHistoryKey(entry) {
  return JSON.stringify([
    entry.command,
    entry.argsString || '',
    entry.cwd || '',
    !!entry.restrictToCwd,
  ])
}

// Split a command-line-like string into tokens.
// Respects simple double and single quotes. No shell substitution.
function tokenize(argsString) {
  const out = []
  let cur = ''
  let quote = null
  for (let i = 0; i < argsString.length; i++) {
    const ch = argsString[i]
    if (quote) {
      if (ch === quote) {
        quote = null
      } else {
        cur += ch
      }
    } else if (ch === '"' || ch === "'") {
      quote = ch
    } else if (ch === ' ' || ch === '\t') {
      if (cur) {
        out.push(cur)
        cur = ''
      }
    } else {
      cur += ch
    }
  }
  if (cur) out.push(cur)
  return out
}

const STATUS_COLORS = {
  running: 'bg-green-900 text-green-300 border-green-700',
  exited: 'bg-gray-700 text-gray-300 border-gray-600',
  cancelled: 'bg-yellow-900 text-yellow-300 border-yellow-700',
  failed: 'bg-red-900 text-red-300 border-red-700',
}

const STREAM_COLORS = {
  stdout: 'text-gray-200',
  stderr: 'text-red-300',
  system: 'text-blue-300 italic',
}

function ProcessListItem({ proc, isSelected, onSelect }) {
  const statusCls = STATUS_COLORS[proc.status] || STATUS_COLORS.exited
  const started = proc.started_at ? new Date(proc.started_at * 1000).toLocaleTimeString() : ''
  return (
    <button
      type="button"
      onClick={() => onSelect(proc.id)}
      className={`w-full text-left p-3 rounded-lg border transition-colors ${
        isSelected
          ? 'bg-gray-700 border-blue-500'
          : 'bg-gray-800 border-gray-700 hover:bg-gray-700'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="font-mono text-sm text-gray-100 truncate flex items-center gap-1">
          {proc.sandboxed && (
            <Shield className="w-3 h-3 text-blue-400" title="Sandboxed to cwd (Landlock)" />
          )}
          {proc.command}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded border ${statusCls}`}>{proc.status}</span>
      </div>
      <div className="text-xs text-gray-400 font-mono truncate">
        {(proc.args || []).join(' ')}
      </div>
      <div className="text-xs text-gray-500 mt-1">
        pid {proc.pid || '-'} · started {started}
        {proc.exit_code !== null && proc.exit_code !== undefined && (
          <> · exit {proc.exit_code}</>
        )}
      </div>
    </button>
  )
}

function StreamView({ process, chunks }) {
  const scrollRef = useRef(null)
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chunks])

  if (!process) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-500">
        <div className="text-center">
          <Terminal className="w-8 h-8 mx-auto mb-2 opacity-50" />
          <p className="text-sm">Select a process to view its output</p>
        </div>
      </div>
    )
  }

  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto p-3 bg-black rounded-lg font-mono text-xs whitespace-pre-wrap break-words"
    >
      {chunks.length === 0 ? (
        <div className="text-gray-500">Waiting for output...</div>
      ) : (
        chunks.map((c, i) => (
          <div key={i} className={STREAM_COLORS[c.stream] || STREAM_COLORS.stdout}>
            {c.text}
          </div>
        ))
      )}
    </div>
  )
}

function AgentPortal() {
  const navigate = useNavigate()
  const [processes, setProcesses] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [chunks, setChunks] = useState([])
  const [selectedProcess, setSelectedProcess] = useState(null)
  const [command, setCommand] = useState('')
  const [argsString, setArgsString] = useState('')
  const [cwd, setCwd] = useState('')
  const [restrictToCwd, setRestrictToCwd] = useState(false)
  const [launchError, setLaunchError] = useState(null)
  const [launching, setLaunching] = useState(false)
  const [listError, setListError] = useState(null)
  const [landlockSupported, setLandlockSupported] = useState(null)
  const [launchHistory, setLaunchHistory] = useState(() => loadLaunchHistory())
  const wsRef = useRef(null)

  useEffect(() => {
    fetch('/api/agent-portal/capabilities', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        if (c && typeof c.landlock_supported === 'boolean') {
          setLandlockSupported(c.landlock_supported)
        }
      })
      .catch(() => {})
  }, [])

  // Prepopulate form from most recent entry on first load (if form is empty)
  useEffect(() => {
    if (!command && launchHistory.length > 0) {
      const last = launchHistory[0]
      setCommand(last.command || '')
      setArgsString(last.argsString || '')
      setCwd(last.cwd || '')
      setRestrictToCwd(!!last.restrictToCwd)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const applyHistoryEntry = (entry) => {
    setCommand(entry.command || '')
    setArgsString(entry.argsString || '')
    setCwd(entry.cwd || '')
    setRestrictToCwd(!!entry.restrictToCwd)
  }

  const removeHistoryEntry = (entry) => {
    const key = makeHistoryKey(entry)
    const next = launchHistory.filter((e) => makeHistoryKey(e) !== key)
    setLaunchHistory(next)
    saveLaunchHistory(next)
  }

  const fetchProcesses = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-portal/processes', { credentials: 'include' })
      if (!res.ok) {
        setListError(`Failed to load processes (${res.status})`)
        return
      }
      const data = await res.json()
      setProcesses(data.processes || [])
      setListError(null)
    } catch (err) {
      setListError(err.message || 'Failed to load processes')
    }
  }, [])

  useEffect(() => {
    fetchProcesses()
    const interval = setInterval(fetchProcesses, 4000)
    return () => clearInterval(interval)
  }, [fetchProcesses])

  // Open a WebSocket to stream whenever selectedId changes
  useEffect(() => {
    if (wsRef.current) {
      try { wsRef.current.close() } catch { /* ignore */ }
      wsRef.current = null
    }
    setChunks([])
    setSelectedProcess(null)
    if (!selectedId) return

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/api/agent-portal/processes/${selectedId}/stream`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'process_info' || msg.type === 'process_end') {
          setSelectedProcess(msg.process)
          if (msg.type === 'process_end') fetchProcesses()
        } else if (msg.type === 'output') {
          setChunks((prev) => [...prev, {
            stream: msg.stream,
            text: msg.text,
            timestamp: msg.timestamp,
          }])
        }
      } catch {
        // Ignore parse failures
      }
    }
    ws.onerror = () => {
      setChunks((prev) => [...prev, { stream: 'system', text: '[stream error]', timestamp: 0 }])
    }

    return () => {
      try { ws.close() } catch { /* ignore */ }
    }
  }, [selectedId, fetchProcesses])

  const handleLaunch = async (e) => {
    e.preventDefault()
    setLaunchError(null)
    setLaunching(true)
    try {
      const trimmedCommand = command.trim()
      const trimmedCwd = cwd.trim()
      const body = {
        command: trimmedCommand,
        args: tokenize(argsString),
        restrict_to_cwd: !!restrictToCwd,
      }
      if (trimmedCwd) body.cwd = trimmedCwd
      const res = await fetch('/api/agent-portal/processes', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        setLaunchError(err.detail || `Launch failed (${res.status})`)
        return
      }
      const proc = await res.json()
      setSelectedId(proc.id)

      // Record history (dedupe by content, newest first)
      const entry = {
        command: trimmedCommand,
        argsString,
        cwd: trimmedCwd,
        restrictToCwd: !!restrictToCwd,
        lastUsed: Date.now(),
      }
      const key = makeHistoryKey(entry)
      const next = [entry, ...launchHistory.filter((e) => makeHistoryKey(e) !== key)]
        .slice(0, LAUNCH_HISTORY_MAX)
      setLaunchHistory(next)
      saveLaunchHistory(next)

      await fetchProcesses()
    } catch (err) {
      setLaunchError(err.message || 'Launch failed')
    } finally {
      setLaunching(false)
    }
  }

  const handleCancel = async () => {
    if (!selectedId) return
    try {
      const res = await fetch(`/api/agent-portal/processes/${selectedId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (res.ok) {
        const proc = await res.json()
        setSelectedProcess(proc)
      }
      await fetchProcesses()
    } catch {
      // ignore
    }
  }

  const canCancel = useMemo(
    () => selectedProcess && selectedProcess.status === 'running',
    [selectedProcess]
  )

  return (
    <div className="flex flex-col h-screen w-full bg-gray-900 text-gray-200">
      <header className="flex items-center justify-between p-3 bg-gray-800 border-b border-gray-700">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Back to chat"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h1 className="text-lg font-semibold">Agent Portal</h1>
            <p className="text-xs text-gray-400">
              Launch host processes and stream their output. Dev preview — no access controls yet.
            </p>
          </div>
        </div>
        <button
          onClick={fetchProcesses}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm"
          title="Refresh process list"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </header>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-[360px_1fr] min-h-0">
        {/* Left column: launcher + process list */}
        <div className="flex flex-col border-r border-gray-700 min-h-0">
          <form onSubmit={handleLaunch} className="p-4 space-y-3 border-b border-gray-700">
            <div>
              <label className="block text-xs uppercase text-gray-400 mb-1">Command</label>
              <input
                type="text"
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="e.g. bash, python, claude"
                className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
            </div>
            <div>
              <label className="block text-xs uppercase text-gray-400 mb-1">Arguments</label>
              <input
                type="text"
                value={argsString}
                onChange={(e) => setArgsString(e.target.value)}
                placeholder='e.g. -c "echo hello; sleep 2; echo done"'
                className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-[11px] text-gray-500 mt-1">
                Whitespace-separated; quote values with spaces. Not a shell.
              </p>
            </div>
            <div>
              <label className="block text-xs uppercase text-gray-400 mb-1">Working directory (optional)</label>
              <input
                type="text"
                value={cwd}
                onChange={(e) => setCwd(e.target.value)}
                placeholder="/home/you/project"
                className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <label className={`flex items-start gap-2 text-xs ${
              !cwd.trim() || landlockSupported === false ? 'opacity-60' : ''
            }`}>
              <input
                type="checkbox"
                checked={restrictToCwd}
                disabled={!cwd.trim() || landlockSupported === false}
                onChange={(e) => setRestrictToCwd(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="inline-flex items-center gap-1 font-medium text-gray-200">
                  <Shield className="w-3.5 h-3.5" /> Restrict to working directory (Landlock)
                </span>
                <span className="block text-[11px] text-gray-500">
                  {landlockSupported === false
                    ? 'Kernel does not support Landlock on this host.'
                    : !cwd.trim()
                    ? 'Set a working directory to enable.'
                    : 'Blocks filesystem writes outside cwd. Not a full sandbox (network/ipc/etc unrestricted).'}
                </span>
              </span>
            </label>
            {launchError && (
              <div className="text-xs text-red-300 bg-red-900/40 border border-red-700 rounded p-2">
                {launchError}
              </div>
            )}
            <button
              type="submit"
              disabled={launching || !command.trim()}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white text-sm font-medium"
            >
              <Play className="w-4 h-4" />
              {launching ? 'Launching...' : 'Launch'}
            </button>
          </form>

          {launchHistory.length > 0 && (
            <div className="p-3 border-b border-gray-700">
              <div className="flex items-center gap-2 text-xs uppercase text-gray-400 mb-2">
                <History className="w-3.5 h-3.5" /> Recent launches
              </div>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {launchHistory.map((entry) => (
                  <div
                    key={makeHistoryKey(entry)}
                    className="flex items-center gap-1 text-xs bg-gray-800 rounded px-2 py-1 border border-gray-700"
                  >
                    <button
                      type="button"
                      onClick={() => applyHistoryEntry(entry)}
                      className="flex-1 min-w-0 text-left font-mono truncate text-gray-200 hover:text-blue-300"
                      title="Use this launch"
                    >
                      {entry.command} {entry.argsString}
                      {entry.restrictToCwd && ' [sandboxed]'}
                    </button>
                    <button
                      type="button"
                      onClick={() => removeHistoryEntry(entry)}
                      className="p-0.5 text-gray-500 hover:text-red-400 flex-shrink-0"
                      title="Remove from history"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            <div className="text-xs uppercase text-gray-400 mb-1">Your processes</div>
            {listError && <div className="text-xs text-red-300">{listError}</div>}
            {processes.length === 0 && !listError && (
              <div className="text-xs text-gray-500">No processes yet.</div>
            )}
            {processes.map((p) => (
              <ProcessListItem
                key={p.id}
                proc={p}
                isSelected={p.id === selectedId}
                onSelect={setSelectedId}
              />
            ))}
          </div>
        </div>

        {/* Right column: stream view */}
        <div className="flex flex-col min-h-0">
          <div className="flex items-center justify-between p-3 border-b border-gray-700 bg-gray-800">
            <div className="min-w-0">
              {selectedProcess ? (
                <>
                  <div className="font-mono text-sm truncate">
                    {selectedProcess.command} {(selectedProcess.args || []).join(' ')}
                  </div>
                  <div className="text-xs text-gray-400">
                    pid {selectedProcess.pid || '-'} · status {selectedProcess.status}
                    {selectedProcess.exit_code !== null && selectedProcess.exit_code !== undefined && (
                      <> · exit {selectedProcess.exit_code}</>
                    )}
                  </div>
                </>
              ) : (
                <div className="text-sm text-gray-400">No process selected</div>
              )}
            </div>
            <button
              onClick={handleCancel}
              disabled={!canCancel}
              className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-700 hover:bg-red-600 disabled:bg-gray-700 disabled:cursor-not-allowed text-white text-sm"
              title="Send SIGTERM to the process"
            >
              <Square className="w-4 h-4" /> Cancel
            </button>
          </div>
          <div className="flex-1 p-3 min-h-0 flex">
            <StreamView process={selectedProcess} chunks={chunks} />
          </div>
        </div>
      </div>
    </div>
  )
}

export default AgentPortal
