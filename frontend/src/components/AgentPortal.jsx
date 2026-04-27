import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, Square, RefreshCw, Shield, History, X, Bookmark, Save, MonitorDot, AlertTriangle, Boxes, Gauge, PanelLeftClose, PanelLeftOpen, Check, Plus, ChevronDown, ChevronRight, LayoutGrid } from 'lucide-react'
import { useToast, useDialog } from './ui/toastContext'
import '@xterm/xterm/css/xterm.css'
import PaneGrid from './agent-portal/PaneGrid'
import { LAYOUT_MODES, SOFT_CAP_LIVE, HARD_CAP_LIVE } from './agent-portal/layoutConstants'
import {
  DEFAULT_LAYOUT,
  normalizeLayout,
  setLayoutMode,
  placeProcessInLayout,
  clearSlot,
  countLiveSlots,
} from './agent-portal/layoutHelpers'
import {
  loadLayoutFromCache,
  saveLayoutToCache,
  fetchLayoutFromServer,
  pushLayoutToServer,
  loadLaunchHistoryFromCache,
  fetchLaunchHistoryFromServer,
  uploadLaunchHistoryToServer,
  upsertLaunchHistoryEntry,
  computeDedupKey,
  deleteLaunchHistoryEntry,
  CACHE_KEYS,
} from './agent-portal/portalStateClient'

const LAUNCH_HISTORY_MAX = 15
const LAUNCH_CONFIGS_KEY = 'atlas.agentPortal.launchConfigs.v1'
const LAUNCH_CONFIGS_MAX = 50

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

// Re-quote any token containing whitespace so round-tripping
//   args=["foo", "bar baz"]  <->  argsString='foo "bar baz"'
// preserves the user's original intent.
function quoteArg(tok) {
  if (!/\s/.test(tok) && !/["']/.test(tok)) return tok
  return '"' + tok.replace(/"/g, '\\"') + '"'
}

// Server presets use snake_case and a fuller field set; the UI has always
// worked in camelCase. Bridge both directions so the existing applyEntry /
// launch plumbing keeps working without touching every caller.
function serverPresetToUi(p) {
  return {
    id: p.id,
    name: p.name,
    description: p.description || '',
    command: p.command || '',
    argsString: Array.isArray(p.args) ? p.args.map(quoteArg).join(' ') : '',
    cwd: p.cwd || '',
    sandboxMode: p.sandbox_mode || 'off',
    extraWritablePaths: p.extra_writable_paths || [],
    usePty: !!p.use_pty,
    namespaces: !!p.namespaces,
    isolateNetwork: !!p.isolate_network,
    memoryLimit: p.memory_limit || null,
    cpuLimit: p.cpu_limit || null,
    pidsLimit: p.pids_limit == null ? null : Number(p.pids_limit),
    displayName: p.display_name || '',
    _source: 'server',
  }
}

function uiToServerPayload(ui) {
  return {
    name: ui.name,
    description: ui.description || '',
    command: ui.command || '',
    // Reuse the form's own tokenize() for round-trip stability (handles
    // quoted tokens the same way the launch path does).
    args: tokenize(ui.argsString || ''),
    cwd: ui.cwd || null,
    sandbox_mode: ui.sandboxMode || 'off',
    extra_writable_paths: ui.extraWritablePaths || [],
    use_pty: !!ui.usePty,
    namespaces: !!ui.namespaces,
    isolate_network: !!ui.isolateNetwork,
    memory_limit: ui.memoryLimit || null,
    cpu_limit: ui.cpuLimit || null,
    pids_limit: ui.pidsLimit == null ? null : Number(ui.pidsLimit),
    display_name: ui.displayName || null,
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

// StreamView and XtermView were merged into the per-pane Pane component
// in components/agent-portal/Pane.jsx so that multiple panes can each
// own their own WebSocket and xterm instance. See PaneGrid.jsx.

function AgentPortal() {
  const navigate = useNavigate()
  const toast = useToast()
  const dialog = useDialog()
  const [launchModalOpen, setLaunchModalOpen] = useState(false)
  const [showRecent, setShowRecent] = useState(false)
  const [processes, setProcesses] = useState([])
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
  // launchConfigs holds the merged UI-shape list shown in the sidebar.
  // Server-backed entries have _source === 'server' and a pst_* id; legacy
  // localStorage-only entries have cfg_* ids.
  const [launchConfigs, setLaunchConfigs] = useState(() => loadLaunchConfigs())
  const [loadedPresetId, setLoadedPresetId] = useState(null)
  const [launchError, setLaunchError] = useState(null)
  const [launching, setLaunching] = useState(false)
  const [listError, setListError] = useState(null)
  const [landlockSupported, setLandlockSupported] = useState(null)
  // Multi-pane layout state. Hydrate from localStorage immediately for
  // first-paint snappiness, then reconcile with the server (PortalStore)
  // once the GET resolves. Both writes are mirrored to localStorage and
  // pushed to the server on every change.
  const [layout, setLayout] = useState(() => normalizeLayout(loadLayoutFromCache()))
  const [focusedSlot, setFocusedSlot] = useState(0)
  const [fullscreenSlot, setFullscreenSlot] = useState(null)
  // launchHistory: same first-paint cache + server reconcile pattern.
  const [launchHistory, setLaunchHistory] = useState(() => loadLaunchHistoryFromCache())

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

  // Fetch presets from the server on mount. If the server has no presets
  // yet but localStorage has legacy ones, migrate them up so the user
  // doesn't lose their library on first upgrade. Subsequent CRUD operations
  // update launchConfigs in place — no periodic refresh.
  useEffect(() => {
    let cancelled = false
    const bootstrap = async () => {
      try {
        const res = await fetch('/api/agent-portal/presets', { credentials: 'include' })
        if (!res.ok) return
        const data = await res.json()
        if (cancelled) return
        const serverUi = (data.presets || []).map(serverPresetToUi)
        if (serverUi.length === 0) {
          // Migrate legacy localStorage entries to the server on first run.
          const legacy = loadLaunchConfigs()
          if (legacy.length > 0) {
            const migrated = []
            for (const cfg of legacy) {
              try {
                const r = await fetch('/api/agent-portal/presets', {
                  method: 'POST',
                  credentials: 'include',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(uiToServerPayload(cfg)),
                })
                if (r.ok) {
                  const created = await r.json()
                  migrated.push(serverPresetToUi(created))
                }
              } catch { /* skip bad entries */ }
            }
            if (!cancelled) {
              setLaunchConfigs(migrated)
              // Clear the legacy key only after successful migration so we
              // don't lose data on a partial failure.
              if (migrated.length === legacy.length) {
                try { localStorage.removeItem(LAUNCH_CONFIGS_KEY) } catch { /* ignore */ }
              }
            }
          }
          return
        }
        setLaunchConfigs(serverUi)
      } catch { /* ignore — keep the localStorage list */ }
    }
    bootstrap()
    return () => { cancelled = true }
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

  const applyEntry = (entry, opts = {}) => {
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
    // Only server-backed presets are "loaded" in the updatable sense.
    if (opts.asPreset && entry._source === 'server' && entry.id) {
      setLoadedPresetId(entry.id)
    } else {
      setLoadedPresetId(null)
    }
  }

  const currentFormAsUiConfig = (nameOverride, descriptionOverride) => ({
    name: nameOverride || '',
    description: descriptionOverride || '',
    command: command.trim(),
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
    displayName: displayName.trim() || null,
  })

  const saveCurrentAsPreset = async () => {
    const trimmedCommand = command.trim()
    if (!trimmedCommand) return
    const defaultName = trimmedCommand + (argsString ? ' ' + argsString.slice(0, 40) : '')
    const answer = await dialog.prompt({
      title: 'Save launch as preset',
      label: 'Name',
      defaultValue: defaultName,
      placeholder: 'e.g. Claude in repo-a',
      secondaryLabel: 'Description (optional)',
      secondaryPlaceholder: 'What is this preset for?',
      okText: 'Save',
      required: true,
    })
    if (!answer) return
    const { value: name, secondary: description = '' } = answer
    const payload = uiToServerPayload(currentFormAsUiConfig(name, description))
    try {
      const res = await fetch('/api/agent-portal/presets', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        toast.error(`Save failed (${res.status})`)
        return
      }
      const created = await res.json()
      setLaunchConfigs((prev) => [serverPresetToUi(created), ...prev])
      setLoadedPresetId(created.id)
      toast.success(`Preset "${created.name}" saved`)
    } catch (err) {
      toast.error(err.message || 'Save failed')
    }
  }

  const updateLoadedPreset = async () => {
    if (!loadedPresetId) return
    const existing = launchConfigs.find((c) => c.id === loadedPresetId)
    if (!existing) return
    const payload = uiToServerPayload(
      currentFormAsUiConfig(existing.name, existing.description)
    )
    try {
      const res = await fetch(`/api/agent-portal/presets/${encodeURIComponent(loadedPresetId)}`, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        toast.error(`Update failed (${res.status})`)
        return
      }
      const updated = await res.json()
      const uiUpdated = serverPresetToUi(updated)
      setLaunchConfigs((prev) => prev.map((c) => (c.id === uiUpdated.id ? uiUpdated : c)))
      toast.success(`Preset "${uiUpdated.name}" updated`)
    } catch (err) {
      toast.error(err.message || 'Update failed')
    }
  }

  const deletePreset = async (id) => {
    const target = launchConfigs.find((c) => c.id === id)
    const confirmed = await dialog.confirm({
      title: 'Delete preset?',
      message: target ? `"${target.name}" will be removed.` : 'This preset will be removed.',
      okText: 'Delete',
      destructive: true,
    })
    if (!confirmed) return
    // Legacy (pre-migration) entries have cfg_* ids and live only in
    // localStorage; drop them locally and rewrite the cache.
    if (!id || !id.startsWith('pst_')) {
      setLaunchConfigs((prev) => {
        const next = prev.filter((c) => c.id !== id)
        saveLaunchConfigs(next)
        return next
      })
      if (loadedPresetId === id) setLoadedPresetId(null)
      toast.info('Preset removed (local)')
      return
    }
    try {
      const res = await fetch(`/api/agent-portal/presets/${encodeURIComponent(id)}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok && res.status !== 404) {
        toast.error(`Delete failed (${res.status})`)
        return
      }
      setLaunchConfigs((prev) => prev.filter((c) => c.id !== id))
      if (loadedPresetId === id) setLoadedPresetId(null)
      toast.success(target ? `Deleted "${target.name}"` : 'Preset deleted')
    } catch (err) {
      toast.error(err.message || 'Delete failed')
    }
  }

  const removeHistoryEntry = async (entry) => {
    const key = makeHistoryKey(entry)
    const next = launchHistory.filter((e) => makeHistoryKey(e) !== key)
    setLaunchHistory(next)
    // Server-side delete uses the PortalStore's own dedup key (a
    // sha256 over the launch identity), distinct from the client-side
    // makeHistoryKey above.
    try {
      const dedup = await computeDedupKey(entry)
      const refreshed = await deleteLaunchHistoryEntry(dedup)
      if (refreshed) setLaunchHistory(refreshed)
    } catch {
      // Cache-only delete is acceptable; server reconciles on next mount.
    }
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

  // ------------------------------------------------------------------
  // Layout — server reconciliation + write-through
  // ------------------------------------------------------------------
  //
  // First paint reads localStorage (DEFAULT_LAYOUT fallback) so the
  // grid appears instantly. The server fetch happens in parallel and
  // overwrites the cache once it lands. After that, every layout
  // mutation is mirrored to localStorage and pushed to the server.

  const layoutHydratedRef = useRef(false)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const remote = await fetchLayoutFromServer()
      if (cancelled) return
      if (remote) {
        const norm = normalizeLayout(remote)
        setLayout(norm)
        saveLayoutToCache(norm)
      } else {
        // Server has nothing — upload whatever the cache had (one-shot
        // migration). Skip if the cache was also empty.
        const cached = loadLayoutFromCache()
        if (cached && cached.mode) {
          await pushLayoutToServer(normalizeLayout(cached))
        }
      }
      layoutHydratedRef.current = true
    })()
    return () => { cancelled = true }
  }, [])

  const updateLayout = useCallback((nextOrUpdater) => {
    setLayout((prev) => {
      const next = typeof nextOrUpdater === 'function' ? nextOrUpdater(prev) : nextOrUpdater
      const norm = normalizeLayout(next)
      saveLayoutToCache(norm)
      // Defer the server push slightly so a burst of layout changes
      // (e.g. switching mode immediately drops/refills slots) doesn't
      // fan out to N HTTP calls.
      pushLayoutToServer(norm)
      return norm
    })
  }, [])

  // Hydrate launch history from the server on mount. Same first-paint
  // pattern as layout: cache shows immediately, server reconciles when
  // it lands, and migrate the cache to the server if the server is
  // empty on first read.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const remote = await fetchLaunchHistoryFromServer()
      if (cancelled) return
      if (remote && remote.length > 0) {
        setLaunchHistory(remote)
      } else {
        const cached = loadLaunchHistoryFromCache()
        if (cached.length > 0) {
          const uploaded = await uploadLaunchHistoryToServer(cached)
          if (!cancelled && uploaded) {
            setLaunchHistory(uploaded)
            // Server is now authoritative; clear the cache key so a
            // future browser without server access doesn't double-show.
            try { localStorage.removeItem(CACHE_KEYS.LAUNCH_HISTORY) } catch { /* ignore */ }
          }
        }
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Build a stable processes-by-id map for PaneGrid so the panes can
  // resolve their slot's process_id to a summary in O(1).
  const processesById = useMemo(() => {
    const out = {}
    for (const p of processes) out[p.id] = p
    return out
  }, [processes])

  // After the process list arrives, drop any layout slot pointing at a
  // process the server no longer knows about (process exited and was
  // garbage-collected). This keeps F5-survival self-healing.
  useEffect(() => {
    if (!layoutHydratedRef.current) return
    const known = new Set(processes.map((p) => p.id))
    setLayout((prev) => {
      let changed = false
      const slots = prev.slots.map((s) => {
        if (s && !known.has(s)) {
          changed = true
          return null
        }
        return s
      })
      if (!changed) return prev
      const next = { ...prev, slots }
      saveLayoutToCache(next)
      pushLayoutToServer(next)
      return next
    })
  }, [processes])

  // Surface an exit toast on non-zero exit codes for any tracked
  // process. Previously this lived inside the single-pane WS handler;
  // now the panes raise process_end via onProcessUpdate and we de-dupe
  // by id so the same exit fires the toast at most once.
  const exitToastShownRef = useRef(new Set())
  const handleProcessUpdate = useCallback((summary) => {
    if (!summary || !summary.id) return
    setProcesses((prev) => {
      const idx = prev.findIndex((p) => p.id === summary.id)
      if (idx >= 0) {
        const next = prev.slice()
        next[idx] = summary
        return next
      }
      return [summary, ...prev]
    })
    if (
      summary.status !== 'running'
      && typeof summary.exit_code === 'number'
      && summary.exit_code !== 0
      && !exitToastShownRef.current.has(summary.id)
    ) {
      exitToastShownRef.current.add(summary.id)
      const label = summary.display_name || summary.command || 'process'
      const hint = summary.exit_code === 127
        ? '\nHint: exit 127 usually means a binary or its shebang interpreter is missing from the child PATH.'
        : ''
      toast.error(`${label} exited with code ${summary.exit_code}.${hint}`, { duration: 8000 })
    }
  }, [toast])

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
      // Slot the new process into the focused slot (or first empty
      // slot) of the current layout. Honor the hard cap so the user
      // can't accidentally launch their way past it.
      const live = countLiveSlots(layout)
      if (live >= HARD_CAP_LIVE) {
        toast.error(`Hit the ${HARD_CAP_LIVE}-pane hard cap. Remove one before launching another.`)
      } else {
        if (live + 1 > SOFT_CAP_LIVE) {
          toast.info(`Soft cap (${SOFT_CAP_LIVE} live panes) reached.`, { duration: 4000 })
        }
        updateLayout((prev) => placeProcessInLayout(prev, proc.id, focusedSlot))
      }
      toast.success(`Launched: ${proc.display_name || proc.command}`)
      setLaunchModalOpen(false)

      // Record history (dedupe by content, newest first). Push to the
      // server first; keep the cache in sync so refresh-before-server-
      // responds still shows it.
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
      const refreshed = await upsertLaunchHistoryEntry(entry)
      if (refreshed) {
        setLaunchHistory(refreshed)
      } else {
        // Server unavailable — keep local-only behavior so the form
        // pre-fill loop still works.
        const key = makeHistoryKey(entry)
        const next = [entry, ...launchHistory.filter((e) => makeHistoryKey(e) !== key)]
          .slice(0, LAUNCH_HISTORY_MAX)
        setLaunchHistory(next)
      }

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
      }
    } catch {
      // ignore
    }
  }, [])

  // Resolve which process is in the focused slot (also drives the
  // header "Cancel" button — it cancels the focused pane).
  const focusedProcessId = layout.slots[focusedSlot] || null
  const focusedProcess = focusedProcessId ? processesById[focusedProcessId] : null

  const handleCancel = async () => {
    if (!focusedProcessId) return
    try {
      const res = await fetch(`/api/agent-portal/processes/${focusedProcessId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (res.ok) {
        const proc = await res.json()
        // Reflect the new status immediately so the header badge updates
        // without waiting for the next 4s poll.
        setProcesses((prev) => prev.map((p) => (p.id === proc.id ? proc : p)))
      }
      await fetchProcesses()
    } catch {
      // ignore
    }
  }

  const canCancel = useMemo(
    () => focusedProcess && focusedProcess.status === 'running',
    [focusedProcess]
  )

  // ProcessListItem in the left rail still shows a "select" affordance.
  // For the multi-pane world that translates to "drop this process into
  // the focused slot" — easier than implementing drag-and-drop today.
  const handleSelectFromList = useCallback((processId) => {
    if (!processId) return
    updateLayout((prev) => {
      // Already on screen? Move focus there.
      const existingIdx = prev.slots.indexOf(processId)
      if (existingIdx >= 0) {
        setFocusedSlot(existingIdx)
        return prev
      }
      return placeProcessInLayout(prev, processId, focusedSlot)
    })
  }, [focusedSlot, updateLayout])

  // Pane-grid callbacks ---------------------------------------------------

  const handleCloseSlot = useCallback((slotIndex) => {
    updateLayout((prev) => clearSlot(prev, slotIndex))
  }, [updateLayout])

  const handleFullscreenSlot = useCallback((slotIndex) => {
    setFullscreenSlot((prev) => (prev === slotIndex ? null : slotIndex))
  }, [])

  // Keyboard shortcuts: F toggles fullscreen for the focused slot, Esc
  // exits fullscreen. Phase 2 (command palette) layers on top of this.
  useEffect(() => {
    const handler = (e) => {
      // Don't swallow keystrokes meant for an input / textarea / xterm.
      const ae = document.activeElement
      const tag = ae?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || ae?.isContentEditable) return
      // Don't fire when an xterm has focus — the terminal owns the keys.
      if (ae?.closest?.('[data-testid="agent-portal-pane"] .xterm')) return
      if (e.key === 'Escape' && fullscreenSlot != null) {
        setFullscreenSlot(null)
        e.preventDefault()
      } else if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (layout.slots[focusedSlot]) {
          setFullscreenSlot((prev) => (prev === focusedSlot ? null : focusedSlot))
          e.preventDefault()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [fullscreenSlot, focusedSlot, layout])

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
            onClick={handleCancel}
            disabled={!canCancel}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-700 hover:bg-red-600 disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed text-white text-sm"
            title="Send SIGTERM to the selected running process"
          >
            <Square className="w-4 h-4" /> <span className="hidden sm:inline">Cancel</span>
          </button>
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
        {/* Left column: active sessions + presets + collapsible recent */}
        <div className={`flex flex-col border-r border-gray-700 min-h-0 overflow-y-auto ${leftCollapsed ? 'hidden' : ''}`}>
          <div className="p-3 border-b border-gray-700">
            <button
              type="button"
              onClick={() => setLaunchModalOpen(true)}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium"
            >
              <Plus className="w-4 h-4" /> New launch
            </button>
          </div>

          <div className="p-3 space-y-2 border-b border-gray-700">
            <div className="text-xs uppercase text-gray-400 mb-1">Active sessions</div>
            {listError && <div className="text-xs text-red-300">{listError}</div>}
            {processes.length === 0 && !listError && (
              <div className="text-xs text-gray-500">No processes yet. Click <strong>New launch</strong> to start one.</div>
            )}
            {processes.map((p) => (
              <ProcessListItem
                key={p.id}
                proc={p}
                isSelected={layout.slots.includes(p.id)}
                onSelect={handleSelectFromList}
                onRename={renameProcess}
              />
            ))}
          </div>

        {/* Launch modal — opens via the "New launch" button above */}
        {launchModalOpen && (
        <div
          className="fixed inset-0 z-[9997] bg-black/60 backdrop-blur-sm flex items-start justify-center p-4 overflow-y-auto"
          onClick={() => setLaunchModalOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="New launch"
        >
        <div
          className="bg-gray-900 border border-gray-700 rounded-xl shadow-2xl w-full max-w-3xl my-4"
          onClick={(e) => e.stopPropagation()}
        >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 sticky top-0 bg-gray-900 rounded-t-xl z-10">
          <h2 className="text-lg font-semibold text-gray-100 flex items-center gap-2">
            <Play className="w-4 h-4 text-blue-400" /> New launch
          </h2>
          <button
            type="button"
            onClick={() => setLaunchModalOpen(false)}
            className="p-1 text-gray-400 hover:text-gray-200"
            aria-label="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
          <form onSubmit={handleLaunch} className="p-5 space-y-4">
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
              {loadedPresetId && (
                <button
                  type="button"
                  onClick={updateLoadedPreset}
                  disabled={!command.trim()}
                  className="flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 disabled:bg-gray-800 disabled:text-gray-500 text-sm"
                  title="Save changes back to the loaded preset"
                >
                  <Check className="w-4 h-4" />
                  <span className="hidden sm:inline">Update</span>
                </button>
              )}
              <button
                type="button"
                onClick={saveCurrentAsPreset}
                disabled={!command.trim()}
                className="flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 disabled:bg-gray-800 disabled:text-gray-500 text-sm"
                title="Save the current form as a new named preset"
              >
                <Save className="w-4 h-4" />
                <span className="hidden sm:inline">Save as…</span>
              </button>
            </div>
          </form>
        </div>
        </div>
        )}

          {launchConfigs.length > 0 && (
            <div className="p-3 border-b border-gray-700">
              <div className="flex items-center justify-between gap-2 text-xs uppercase text-gray-400 mb-2">
                <span className="flex items-center gap-2">
                  <Bookmark className="w-3.5 h-3.5" /> Presets library
                </span>
              </div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {launchConfigs.map((cfg) => {
                  const isLoaded = cfg.id === loadedPresetId
                  return (
                    <div
                      key={cfg.id}
                      className={`flex items-center gap-1 text-xs rounded px-2 py-1 border ${
                        isLoaded
                          ? 'bg-indigo-900/40 border-indigo-600'
                          : 'bg-gray-800 border-gray-700'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => {
                          applyEntry(cfg, { asPreset: true })
                          setLaunchModalOpen(true)
                        }}
                        className="flex-1 min-w-0 text-left truncate hover:text-blue-300"
                        title={
                          (cfg.description ? `${cfg.description}\n\n` : '') +
                          `${cfg.command} ${cfg.argsString || ''}` +
                          (normalizeSandboxMode(cfg) !== 'off' ? ` [${normalizeSandboxMode(cfg)}]` : '')
                        }
                      >
                        <span className="font-medium text-gray-100">{cfg.name}</span>
                        <span className="block text-[10px] text-gray-500 font-mono truncate">
                          {cfg.command} {cfg.argsString}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => deletePreset(cfg.id)}
                        className="p-0.5 text-gray-500 hover:text-red-400 flex-shrink-0"
                        title="Delete preset"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {launchHistory.length > 0 && (
            <div className="p-3 border-b border-gray-700">
              <button
                type="button"
                onClick={() => setShowRecent((v) => !v)}
                className="w-full flex items-center gap-2 text-xs uppercase text-gray-400 hover:text-gray-200"
                aria-expanded={showRecent}
              >
                {showRecent ? (
                  <ChevronDown className="w-3.5 h-3.5" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5" />
                )}
                <History className="w-3.5 h-3.5" />
                <span>Recent launches</span>
                <span className="text-gray-500 normal-case">({launchHistory.length})</span>
              </button>
              {showRecent && (
                <div className="space-y-1 max-h-40 overflow-y-auto mt-2">
                  {launchHistory.map((entry) => (
                    <div
                      key={makeHistoryKey(entry)}
                      className="flex items-center gap-1 text-xs bg-gray-800 rounded px-2 py-1 border border-gray-700"
                    >
                      <button
                        type="button"
                        onClick={() => {
                          applyEntry(entry)
                          setLaunchModalOpen(true)
                        }}
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
              )}
            </div>
          )}
        </div>

        {/* Right column: multi-pane grid + layout controls */}
        <div className="flex flex-col min-h-0">
          {fullscreenSlot == null && (
            <div className="flex items-center justify-between gap-3 px-3 py-1.5 border-b border-gray-700 bg-gray-800">
              <div className="min-w-0 flex-1">
                {focusedProcess ? (
                  <div className="flex items-center gap-2 text-xs text-gray-300 truncate">
                    <span className="font-mono text-gray-100 truncate">
                      {focusedProcess.display_name?.trim()
                        || `${focusedProcess.command} ${(focusedProcess.args || []).join(' ')}`}
                    </span>
                    <span className="text-gray-500">·</span>
                    <span>pid {focusedProcess.pid || '-'}</span>
                    <span className="text-gray-500">·</span>
                    <span>{focusedProcess.status}</span>
                    {focusedProcess.exit_code !== null && focusedProcess.exit_code !== undefined && (
                      <>
                        <span className="text-gray-500">·</span>
                        <span>exit {focusedProcess.exit_code}</span>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400">
                    {processes.length === 0
                      ? 'No processes yet — click New launch.'
                      : 'Click a process in the list to drop it into the focused slot.'}
                  </div>
                )}
              </div>
              <LayoutSwitcher
                mode={layout.mode}
                onChange={(m) => updateLayout((prev) => setLayoutMode(prev, m))}
              />
            </div>
          )}
          <div className="flex-1 min-h-0">
            <PaneGrid
              mode={layout.mode}
              slots={layout.slots}
              processesById={processesById}
              focusedSlot={focusedSlot}
              fullscreenSlot={fullscreenSlot}
              onFocusSlot={setFocusedSlot}
              onCloseSlot={handleCloseSlot}
              onFullscreenSlot={handleFullscreenSlot}
              onRenameProcess={renameProcess}
              onProcessUpdate={handleProcessUpdate}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

// Tiny inline component — kept here rather than in agent-portal/ because
// it has no logic beyond rendering one button per layout mode and would
// just be import noise.
function LayoutSwitcher({ mode, onChange }) {
  const labels = { single: '1', '2x2': '2×2', '3x2': '3×2', 'focus+strip': 'Focus' }
  return (
    <div className="flex items-center gap-1" title="Layout mode">
      <LayoutGrid className="w-3.5 h-3.5 text-gray-400" />
      {LAYOUT_MODES.map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          className={`px-2 py-0.5 text-xs rounded border transition-colors ${
            mode === m
              ? 'bg-blue-600 border-blue-500 text-white'
              : 'bg-gray-700 border-gray-600 text-gray-300 hover:bg-gray-600'
          }`}
        >
          {labels[m] || m}
        </button>
      ))}
    </div>
  )
}

export default AgentPortal
