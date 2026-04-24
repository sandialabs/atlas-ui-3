import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, Square, RefreshCw, Terminal, Shield, History, X, Bookmark, Save, MonitorDot, AlertTriangle, Boxes, Gauge, PanelLeftClose, PanelLeftOpen, Edit2, Check } from 'lucide-react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const LAUNCH_HISTORY_KEY = 'atlas.agentPortal.launchHistory.v1'
const LAUNCH_HISTORY_MAX = 15
const LAUNCH_CONFIGS_KEY = 'atlas.agentPortal.launchConfigs.v1'
const LAUNCH_CONFIGS_MAX = 50

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

function normalizeSandboxMode(entry) {
  if (entry.sandboxMode) return entry.sandboxMode
  if (entry.restrictToCwd) return 'strict'
  return 'off'
}

function loadLaunchConfigs() {
  try {
    const raw = localStorage.getItem(LAUNCH_CONFIGS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((e) => e && typeof e.name === 'string' && typeof e.command === 'string')
      .slice(0, LAUNCH_CONFIGS_MAX)
  } catch {
    return []
  }
}

function saveLaunchConfigs(configs) {
  try {
    localStorage.setItem(LAUNCH_CONFIGS_KEY, JSON.stringify(configs.slice(0, LAUNCH_CONFIGS_MAX)))
  } catch {
    // localStorage may be disabled; ignore
  }
}

function makeHistoryKey(entry) {
  return JSON.stringify([
    entry.command,
    entry.argsString || '',
    entry.cwd || '',
    normalizeSandboxMode(entry),
  ])
}

const SANDBOX_MODE_OPTIONS = [
  {
    value: 'off',
    label: 'No sandbox',
    description: 'Child runs with the server process\' normal permissions.',
  },
  {
    value: 'workspace-write',
    label: 'Writes confined to workspace (reads allowed everywhere)',
    description: 'Landlock: the child can read/exec any file on the host, but can only write under the working directory and /dev. Best for tools like cline that need to read configs or invoke interpreters outside cwd.',
  },
  {
    value: 'strict',
    label: 'Strict workspace sandbox (reads restricted)',
    description: 'Landlock: reads restricted to /usr /lib /bin /etc /opt /proc /sys /dev and the target binary\'s directory; writes only under the working directory and /dev.',
  },
]

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

function ProcessListItem({ proc, isSelected, onSelect, onRename }) {
  const statusCls = STATUS_COLORS[proc.status] || STATUS_COLORS.exited
  const started = proc.started_at ? new Date(proc.started_at * 1000).toLocaleTimeString() : ''
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(proc.display_name || '')

  useEffect(() => {
    if (!editing) setDraft(proc.display_name || '')
  }, [proc.display_name, editing])

  const displayTitle = proc.display_name?.trim() || proc.command

  const commit = () => {
    setEditing(false)
    const newName = draft.trim()
    if (newName !== (proc.display_name || '').trim()) {
      onRename(proc.id, newName)
    }
  }

  return (
    <div
      className={`p-3 rounded-lg border transition-colors ${
        isSelected
          ? 'bg-gray-700 border-blue-500'
          : 'bg-gray-800 border-gray-700 hover:bg-gray-700'
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commit()
              else if (e.key === 'Escape') { setEditing(false); setDraft(proc.display_name || '') }
            }}
            className="flex-1 min-w-0 bg-gray-900 border border-gray-600 rounded px-2 py-0.5 text-sm text-gray-100"
          />
        ) : (
          <button
            type="button"
            onClick={() => onSelect(proc.id)}
            className="flex-1 min-w-0 text-left text-sm text-gray-100 truncate flex items-center gap-1"
          >
            {proc.sandboxed && (
              <Shield className="w-3 h-3 text-blue-400 flex-shrink-0" title="Sandboxed (Landlock)" />
            )}
            {proc.namespaces && (
              <Boxes className="w-3 h-3 text-purple-400 flex-shrink-0" title="Isolated namespaces" />
            )}
            <span className="truncate font-medium">{displayTitle}</span>
          </button>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (editing) commit()
            else setEditing(true)
          }}
          className="p-1 text-gray-500 hover:text-blue-300 flex-shrink-0"
          title={editing ? 'Save name' : 'Rename'}
        >
          {editing ? <Check className="w-3 h-3" /> : <Edit2 className="w-3 h-3" />}
        </button>
        <span className={`text-xs px-2 py-0.5 rounded border ${statusCls}`}>{proc.status}</span>
      </div>
      <button
        type="button"
        onClick={() => onSelect(proc.id)}
        className="w-full text-left"
      >
        <div className="text-xs text-gray-400 font-mono truncate">
          {proc.command} {(proc.args || []).join(' ')}
        </div>
        <div className="text-xs text-gray-500 mt-1">
          pid {proc.pid || '-'} · {started}
          {proc.exit_code !== null && proc.exit_code !== undefined && (
            <> · exit {proc.exit_code}</>
          )}
        </div>
      </button>
    </div>
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

// xterm.js terminal view for pty-backed processes. Registers itself
// on `termHandleRef` so the parent WebSocket handler can push raw
// bytes in, and sends keystrokes + resize events back over the WS.
function XtermView({ process, wsRef, termHandleRef }) {
  const hostRef = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)

  useEffect(() => {
    if (!hostRef.current) return
    const term = new XTerm({
      convertEol: false,
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, monospace',
      fontSize: 13,
      theme: {
        background: '#000000',
        foreground: '#e5e7eb',
      },
      allowProposedApi: true,
      scrollback: 5000,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(hostRef.current)
    try { fit.fit() } catch { /* container not sized yet */ }
    termRef.current = term
    fitRef.current = fit

    const sendResize = () => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== 1) return
      try {
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      } catch { /* ignore */ }
    }

    // Forward keystrokes to the backend as base64 so binary-safe.
    const onDataDisp = term.onData((data) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== 1) return
      const bytes = new TextEncoder().encode(data)
      let bin = ''
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      ws.send(JSON.stringify({ type: 'input', data: btoa(bin) }))
    })

    const onResizeDisp = term.onResize(sendResize)

    const doFit = () => {
      try { fit.fit() } catch { /* ignore */ }
    }
    doFit()
    sendResize()
    const ro = new ResizeObserver(doFit)
    ro.observe(hostRef.current)
    window.addEventListener('resize', doFit)

    // Expose a writer so the WS handler can push bytes in.
    termHandleRef.current = {
      write(base64Data) {
        const bin = atob(base64Data)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        term.write(bytes)
      },
      clear() { term.clear() },
    }

    return () => {
      termHandleRef.current = null
      ro.disconnect()
      window.removeEventListener('resize', doFit)
      onDataDisp.dispose()
      onResizeDisp.dispose()
      term.dispose()
    }
  }, [process?.id, wsRef, termHandleRef])

  return (
    <div className="flex-1 min-h-0 w-full bg-black rounded-lg overflow-hidden p-2">
      <div ref={hostRef} className="h-full w-full" />
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
  const [sandboxMode, setSandboxMode] = useState('off')
  const [extraWritablePathsText, setExtraWritablePathsText] = useState('')
  const [usePty, setUsePty] = useState(false)
  const [namespaces, setNamespaces] = useState(false)
  const [isolateNetwork, setIsolateNetwork] = useState(false)
  const [memoryLimit, setMemoryLimit] = useState('')
  const [cpuLimit, setCpuLimit] = useState('')
  const [pidsLimit, setPidsLimit] = useState('')
  const [namespacesSupported, setNamespacesSupported] = useState(null)
  const [cgroupsSupported, setCgroupsSupported] = useState(null)
  const [displayName, setDisplayName] = useState('')
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [launchConfigs, setLaunchConfigs] = useState(() => loadLaunchConfigs())
  const [launchError, setLaunchError] = useState(null)
  const [launching, setLaunching] = useState(false)
  const [listError, setListError] = useState(null)
  const [landlockSupported, setLandlockSupported] = useState(null)
  const [launchHistory, setLaunchHistory] = useState(() => loadLaunchHistory())
  const wsRef = useRef(null)
  const termHandleRef = useRef(null)

  useEffect(() => {
    fetch('/api/agent-portal/capabilities', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((c) => {
        if (c && typeof c.landlock_supported === 'boolean') {
          setLandlockSupported(c.landlock_supported)
        }
        if (c && typeof c.namespaces_supported === 'boolean') {
          setNamespacesSupported(c.namespaces_supported)
        }
        if (c && typeof c.cgroups_supported === 'boolean') {
          setCgroupsSupported(c.cgroups_supported)
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
      setSandboxMode(normalizeSandboxMode(last))
      setExtraWritablePathsText((last.extraWritablePaths || []).join('\n'))
      setUsePty(!!last.usePty)
      setNamespaces(!!last.namespaces)
      setIsolateNetwork(!!last.isolateNetwork)
      setMemoryLimit(last.memoryLimit || '')
      setCpuLimit(last.cpuLimit || '')
      setPidsLimit(last.pidsLimit == null ? '' : String(last.pidsLimit))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const applyEntry = (entry) => {
    setCommand(entry.command || '')
    setArgsString(entry.argsString || '')
    setCwd(entry.cwd || '')
    setSandboxMode(normalizeSandboxMode(entry))
    setExtraWritablePathsText((entry.extraWritablePaths || []).join('\n'))
    setUsePty(!!entry.usePty)
    setNamespaces(!!entry.namespaces)
    setIsolateNetwork(!!entry.isolateNetwork)
    setMemoryLimit(entry.memoryLimit || '')
    setCpuLimit(entry.cpuLimit || '')
    setPidsLimit(entry.pidsLimit == null ? '' : String(entry.pidsLimit))
  }

  const saveCurrentAsConfig = () => {
    const trimmedCommand = command.trim()
    if (!trimmedCommand) return
    const defaultName = trimmedCommand + (argsString ? ' ' + argsString.slice(0, 40) : '')
    const name = window.prompt('Name this launch config:', defaultName)
    if (!name) return
    const config = {
      id: `cfg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      name: name.trim(),
      command: trimmedCommand,
      argsString,
      cwd: cwd.trim(),
      sandboxMode,
      extraWritablePaths: extraWritablePathsText
        .split('\n').map((s) => s.trim()).filter(Boolean),
      usePty,
      namespaces,
      isolateNetwork,
      memoryLimit: memoryLimit.trim() || null,
      cpuLimit: cpuLimit.trim() || null,
      pidsLimit: pidsLimit.trim() ? parseInt(pidsLimit, 10) : null,
    }
    const next = [config, ...launchConfigs.filter((c) => c.name !== name.trim())]
      .slice(0, LAUNCH_CONFIGS_MAX)
    setLaunchConfigs(next)
    saveLaunchConfigs(next)
  }

  const deleteConfig = (id) => {
    const next = launchConfigs.filter((c) => c.id !== id)
    setLaunchConfigs(next)
    saveLaunchConfigs(next)
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
        } else if (msg.type === 'output_raw') {
          // pty mode: push raw bytes straight into xterm.js
          const h = termHandleRef.current
          if (h) h.write(msg.data)
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
      const extraWritable = extraWritablePathsText
        .split('\n').map((s) => s.trim()).filter(Boolean)
      const body = {
        command: trimmedCommand,
        args: tokenize(argsString),
        sandbox_mode: sandboxMode,
        extra_writable_paths: extraWritable,
        use_pty: usePty,
        namespaces,
        isolate_network: isolateNetwork,
        display_name: displayName.trim(),
      }
      if (trimmedCwd) body.cwd = trimmedCwd
      if (memoryLimit.trim()) body.memory_limit = memoryLimit.trim()
      if (cpuLimit.trim()) body.cpu_limit = cpuLimit.trim()
      if (pidsLimit.trim()) body.pids_limit = parseInt(pidsLimit, 10)
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
        sandboxMode,
        extraWritablePaths: extraWritable,
        usePty,
        namespaces,
        isolateNetwork,
        memoryLimit: memoryLimit.trim() || null,
        cpuLimit: cpuLimit.trim() || null,
        pidsLimit: pidsLimit.trim() ? parseInt(pidsLimit, 10) : null,
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

  const renameProcess = useCallback(async (id, newName) => {
    try {
      const res = await fetch(`/api/agent-portal/processes/${id}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_name: newName }),
      })
      if (res.ok) {
        const updated = await res.json()
        setProcesses((prev) => prev.map((p) => (p.id === updated.id ? updated : p)))
        setSelectedProcess((prev) => (prev && prev.id === updated.id ? updated : prev))
      }
    } catch {
      // ignore
    }
  }, [])

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
      <div
        role="alert"
        className="flex items-center gap-3 px-4 py-2 bg-red-900/70 border-b-2 border-red-500 text-red-100 text-sm"
      >
        <AlertTriangle className="w-5 h-5 flex-shrink-0 text-red-300" />
        <div className="flex-1 min-w-0">
          <span className="font-semibold uppercase tracking-wide text-red-200">Dev preview</span>
          <span className="mx-2 opacity-60">·</span>
          <span>
            This page gives the browser direct access to launch and control host
            processes. <strong>Do not enable in production.</strong> No allow-list,
            quotas, or audit trail are in place yet.
          </span>
        </div>
      </div>
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
              Launch host processes and stream their output.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setLeftCollapsed((v) => !v)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm"
            title={leftCollapsed ? 'Show launcher panel' : 'Hide launcher panel (expand output)'}
          >
            {leftCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
            <span className="hidden sm:inline">{leftCollapsed ? 'Show panel' : 'Hide panel'}</span>
          </button>
          <button
            onClick={fetchProcesses}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm"
            title="Refresh process list"
          >
            <RefreshCw className="w-4 h-4" /> <span className="hidden sm:inline">Refresh</span>
          </button>
        </div>
      </header>

      <div
        className={`flex-1 grid grid-cols-1 min-h-0 ${
          leftCollapsed ? '' : 'lg:grid-cols-[360px_1fr]'
        }`}
      >
        {/* Left column: launcher + process list */}
        <div className={`flex flex-col border-r border-gray-700 min-h-0 overflow-y-auto ${leftCollapsed ? 'hidden' : ''}`}>
          <form onSubmit={handleLaunch} className="p-4 space-y-3 border-b border-gray-700">
            <div>
              <label className="block text-xs uppercase text-gray-400 mb-1">Name (optional)</label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="e.g. cline diagram run"
                className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-[11px] text-gray-500 mt-1">
                Shown in the process list. You can edit it later on each process.
              </p>
            </div>
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
            <div className={(!cwd.trim() && sandboxMode !== 'off') || landlockSupported === false ? 'opacity-60' : ''}>
              <label className="block text-xs uppercase text-gray-400 mb-1">
                <span className="inline-flex items-center gap-1">
                  <Shield className="w-3.5 h-3.5" /> Sandbox
                </span>
              </label>
              <select
                value={sandboxMode}
                onChange={(e) => setSandboxMode(e.target.value)}
                disabled={landlockSupported === false}
                className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {SANDBOX_MODE_OPTIONS.map((o) => (
                  <option
                    key={o.value}
                    value={o.value}
                    disabled={o.value !== 'off' && (!cwd.trim() || landlockSupported === false)}
                  >
                    {o.label}
                  </option>
                ))}
              </select>
              <p className="text-[11px] text-gray-500 mt-1">
                {landlockSupported === false
                  ? 'Kernel does not support Landlock on this host; sandbox modes are unavailable.'
                  : !cwd.trim() && sandboxMode !== 'off'
                  ? 'Set a working directory to use a sandbox mode.'
                  : SANDBOX_MODE_OPTIONS.find((o) => o.value === sandboxMode)?.description}
              </p>
            </div>

            <label className="flex items-start gap-2 text-xs">
              <input
                type="checkbox"
                checked={usePty}
                onChange={(e) => setUsePty(e.target.checked)}
                className="mt-0.5"
              />
              <span>
                <span className="inline-flex items-center gap-1 font-medium text-gray-200">
                  <MonitorDot className="w-3.5 h-3.5" /> Allocate a pseudo-terminal (PTY)
                </span>
                <span className="block text-[11px] text-gray-500">
                  The child sees stdout as a TTY, so line-buffered / TUI output (cline, progress
                  bars, colored logs) streams in real time instead of being stuck in libc's block
                  buffer. stdout and stderr are merged.
                </span>
              </span>
            </label>

            <div className={namespacesSupported === false ? 'opacity-60' : ''}>
              <label className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={namespaces}
                  disabled={namespacesSupported === false}
                  onChange={(e) => {
                    setNamespaces(e.target.checked)
                    if (!e.target.checked) setIsolateNetwork(false)
                  }}
                  className="mt-0.5"
                />
                <span>
                  <span className="inline-flex items-center gap-1 font-medium text-gray-200">
                    <Boxes className="w-3.5 h-3.5" /> Isolate Linux namespaces (user, pid, uts, ipc, mnt)
                  </span>
                  <span className="block text-[11px] text-gray-500">
                    {namespacesSupported === false
                      ? 'unshare(1) or unprivileged user namespaces not available on this host.'
                      : 'The child runs with its own pid tree (no host processes visible), isolated hostname/ipc, mounted /proc, and the invoking user mapped to UID 0 inside.'}
                  </span>
                </span>
              </label>
              {namespaces && (
                <label className="flex items-start gap-2 text-xs mt-1 ml-5">
                  <input
                    type="checkbox"
                    checked={isolateNetwork}
                    onChange={(e) => setIsolateNetwork(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="font-medium text-gray-200">Also isolate the network</span>
                    <span className="block text-[11px] text-gray-500">
                      Drops the child into an empty net namespace -- no external connections. Leave off for tools that need LLM API access.
                    </span>
                  </span>
                </label>
              )}
            </div>

            <div className={cgroupsSupported === false ? 'opacity-60' : ''}>
              <label className="block text-xs uppercase text-gray-400 mb-1">
                <span className="inline-flex items-center gap-1">
                  <Gauge className="w-3.5 h-3.5" /> Resource limits (cgroup)
                </span>
              </label>
              <div className="grid grid-cols-3 gap-2">
                <div>
                  <input
                    type="text"
                    value={memoryLimit}
                    onChange={(e) => setMemoryLimit(e.target.value)}
                    placeholder="Mem (e.g. 512M)"
                    disabled={cgroupsSupported === false}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <input
                    type="text"
                    value={cpuLimit}
                    onChange={(e) => setCpuLimit(e.target.value)}
                    placeholder="CPU (e.g. 50%)"
                    disabled={cgroupsSupported === false}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div>
                  <input
                    type="text"
                    value={pidsLimit}
                    onChange={(e) => setPidsLimit(e.target.value)}
                    placeholder="PIDs (e.g. 200)"
                    disabled={cgroupsSupported === false}
                    className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>
              <p className="text-[11px] text-gray-500 mt-1">
                {cgroupsSupported === false
                  ? 'systemd-run --user --scope unavailable on this host; cgroup limits cannot be applied.'
                  : 'Passed to systemd-run --user --scope as MemoryMax / CPUQuota / TasksMax. Leave blank to skip.'}
              </p>
            </div>

            {sandboxMode !== 'off' && (
              <div>
                <label className="block text-xs uppercase text-gray-400 mb-1">
                  Extra read + write paths (one per line)
                </label>
                <textarea
                  value={extraWritablePathsText}
                  onChange={(e) => setExtraWritablePathsText(e.target.value)}
                  placeholder={'~/.cline\n~/.cache/cline'}
                  rows={3}
                  className="w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="text-[11px] text-gray-500 mt-1">
                  Each directory gets the <strong>same full read + write + exec access</strong> as the
                  workspace. Use for tool cache/log/config locations the child needs to both read
                  and write (e.g. <code>~/.cline</code>, <code>~/.cache/&lt;tool&gt;</code>). Created
                  if missing.
                </p>
              </div>
            )}
            {launchError && (
              <div className="text-xs text-red-300 bg-red-900/40 border border-red-700 rounded p-2">
                {launchError}
              </div>
            )}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={launching || !command.trim()}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 disabled:cursor-not-allowed text-white text-sm font-medium"
              >
                <Play className="w-4 h-4" />
                {launching ? 'Launching...' : 'Launch'}
              </button>
              <button
                type="button"
                onClick={saveCurrentAsConfig}
                disabled={!command.trim()}
                className="flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-500 text-sm"
                title="Save the current form as a named launch config"
              >
                <Save className="w-4 h-4" />
                <span className="hidden sm:inline">Save</span>
              </button>
            </div>
          </form>

          {launchConfigs.length > 0 && (
            <div className="p-3 border-b border-gray-700">
              <div className="flex items-center gap-2 text-xs uppercase text-gray-400 mb-2">
                <Bookmark className="w-3.5 h-3.5" /> Saved configs
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {launchConfigs.map((cfg) => (
                  <div
                    key={cfg.id}
                    className="flex items-center gap-1 text-xs bg-gray-800 rounded px-2 py-1 border border-gray-700"
                  >
                    <button
                      type="button"
                      onClick={() => applyEntry(cfg)}
                      className="flex-1 min-w-0 text-left truncate hover:text-blue-300"
                      title={`${cfg.command} ${cfg.argsString || ''}${normalizeSandboxMode(cfg) !== 'off' ? ` [${normalizeSandboxMode(cfg)}]` : ''}`}
                    >
                      <span className="font-medium text-gray-100">{cfg.name}</span>
                      <span className="block text-[10px] text-gray-500 font-mono truncate">
                        {cfg.command} {cfg.argsString}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteConfig(cfg.id)}
                      className="p-0.5 text-gray-500 hover:text-red-400 flex-shrink-0"
                      title="Delete config"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

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
                      onClick={() => applyEntry(entry)}
                      className="flex-1 min-w-0 text-left font-mono truncate text-gray-200 hover:text-blue-300"
                      title="Use this launch"
                    >
                      {entry.command} {entry.argsString}
                      {normalizeSandboxMode(entry) !== 'off' && ` [${normalizeSandboxMode(entry)}]`}
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

          <div className="flex-1 p-3 space-y-2">
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
                onRename={renameProcess}
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
            {selectedProcess && selectedProcess.use_pty ? (
              <XtermView
                process={selectedProcess}
                wsRef={wsRef}
                termHandleRef={termHandleRef}
              />
            ) : (
              <StreamView process={selectedProcess} chunks={chunks} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default AgentPortal
