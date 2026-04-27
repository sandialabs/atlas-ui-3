import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Play, Square, RefreshCw, Shield, History, X, Bookmark, Save, MonitorDot, AlertTriangle, Boxes, Gauge, PanelLeftClose, PanelLeftOpen, Check, Plus, ChevronDown, ChevronRight, LayoutGrid, Edit2 } from 'lucide-react'
import { useToast, useDialog } from './ui/toastContext'
import '@xterm/xterm/css/xterm.css'
import PaneGrid from './agent-portal/PaneGrid'
import CommandPalette from './agent-portal/CommandPalette'
import { LAYOUT_MODES, SOFT_CAP_LIVE, HARD_CAP_LIVE } from './agent-portal/layoutConstants'
import {
  normalizeLayout,
  setLayoutMode,
  placeProcessInLayout,
  clearSlot,
  countLiveSlots,
  moveProcessToSlot,
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
import {
  listGroups,
  createGroup,
  deleteGroup,
  cancelGroup,
  listBundles,
  launchBundle,
  listAudit,
  pauseGroup,
  resumeGroup,
  snapshotGroup,
} from './agent-portal/groupsClient'

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
  // Command palette open state. Toggled by Ctrl-Shift-P; closed on Esc
  // or after running an action.
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Groups (Phase 3): server-enforced collections of panes with
  // shared budgets. Surfaced as a left-rail section + palette actions.
  // The list itself is a thin mirror of /api/agent-portal/groups; group
  // membership of running processes comes from each process's group_id.
  const [groups, setGroups] = useState([])
  // Which group future launches drop into. null = no group.
  const [activeGroupId, setActiveGroupId] = useState(null)
  // Bundles (Phase 4): named multi-preset launches. Listed via the
  // command palette ("Launch bundle: <name>") rather than a dedicated
  // sidebar section to keep the chrome compact.
  const [bundles, setBundles] = useState([])
  // Audit log overlay (Phase 4). Closed by default; opened by the
  // command palette or via a header button.
  const [auditOpen, setAuditOpen] = useState(false)
  const [auditEvents, setAuditEvents] = useState([])
  // Synchronize-input (Phase 5). Set of group_ids with broadcast on.
  // Off by default per the action plan — sync is opt-in and the
  // affordance (colored border) is unmistakable.
  const [syncedGroupIds, setSyncedGroupIds] = useState(() => new Set())
  const toggleGroupSync = useCallback((groupId) => {
    setSyncedGroupIds((prev) => {
      const next = new Set(prev)
      if (next.has(groupId)) next.delete(groupId)
      else next.add(groupId)
      return next
    })
  }, [])

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

  // ------------------------------------------------------------------
  // Groups — fetch + helpers
  // ------------------------------------------------------------------
  // Hydrate the group list once on mount; refresh after any mutation.
  // No periodic polling — group definitions change rarely and the
  // mutating actions (create/delete/cancel) all refresh inline.

  const refreshGroups = useCallback(async () => {
    const list = await listGroups()
    setGroups(list)
    // If the active group was deleted out from under us, drop it.
    setActiveGroupId((prev) => (prev && !list.some((g) => g.id === prev) ? null : prev))
  }, [])

  useEffect(() => {
    refreshGroups()
  }, [refreshGroups])

  const handleCreateGroup = useCallback(async () => {
    const answer = await dialog.prompt({
      title: 'New group',
      label: 'Name',
      placeholder: 'e.g. demo-team',
      secondaryLabel: 'Max panes (optional)',
      secondaryPlaceholder: '4',
      okText: 'Create',
      required: true,
    })
    if (!answer) return
    const max = answer.secondary ? parseInt(answer.secondary, 10) : null
    const created = await createGroup({
      name: (answer.value || '').trim(),
      max_panes: Number.isFinite(max) ? max : null,
    })
    if (created) {
      toast.success(`Group "${created.name}" created`)
      setActiveGroupId(created.id)
      refreshGroups()
    } else {
      toast.error('Group create failed')
    }
  }, [dialog, refreshGroups, toast])

  const handleDeleteGroup = useCallback(async (groupId) => {
    const target = groups.find((g) => g.id === groupId)
    const ok = await dialog.confirm({
      title: 'Delete group?',
      message: target
        ? `"${target.name}" will be deleted and all its running panes will be cancelled.`
        : 'This group will be deleted.',
      okText: 'Delete',
      destructive: true,
    })
    if (!ok) return
    const success = await deleteGroup(groupId)
    if (success) {
      toast.success('Group deleted')
      refreshGroups()
      fetchProcesses()
    } else {
      toast.error('Group delete failed')
    }
  }, [dialog, fetchProcesses, groups, refreshGroups, toast])

  const handleCancelGroup = useCallback(async (groupId) => {
    const result = await cancelGroup(groupId)
    if (result) {
      toast.success(`Cancelled ${result.cancelled?.length || 0} pane(s)`)
      fetchProcesses()
    } else {
      toast.error('Cancel group failed')
    }
  }, [fetchProcesses, toast])

  // Pause / Resume / Snapshot (Phase 6 polish).
  const handlePauseGroup = useCallback(async (groupId) => {
    const res = await pauseGroup(groupId)
    if (res?.paused) toast.info(`Paused ${res.paused.length} pane(s)`)
    else toast.error('Pause failed')
  }, [toast])
  const handleResumeGroup = useCallback(async (groupId) => {
    const res = await resumeGroup(groupId)
    if (res?.resumed) toast.success(`Resumed ${res.resumed.length} pane(s)`)
    else toast.error('Resume failed')
  }, [toast])
  const handleSnapshotGroup = useCallback(async (groupId) => {
    const res = await snapshotGroup(groupId)
    if (!res) {
      toast.error('Snapshot failed')
      return
    }
    // Offer the snapshot as a downloadable JSON. Defer the heavy
    // tarball-of-scrollback packaging to a later iteration; JSON is
    // small enough for typical session sizes and any text editor can
    // open it.
    try {
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `agent-portal-snapshot-${groupId}-${Date.now()}.json`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast.success('Snapshot downloaded')
    } catch (e) {
      toast.error(e.message || 'Snapshot save failed')
    }
  }, [toast])

  // Bundles + audit (Phase 4)
  const refreshBundles = useCallback(async () => {
    setBundles(await listBundles())
  }, [])

  useEffect(() => {
    refreshBundles()
  }, [refreshBundles])

  const handleLaunchBundle = useCallback(async (bundleId) => {
    const target = bundles.find((b) => b.id === bundleId)
    const res = await launchBundle(bundleId)
    if (res?.processes) {
      toast.success(`Launched ${res.processes.length} pane(s) into "${res.group?.name || 'group'}"`)
      // Drop the new processes into available slots so the user actually
      // sees them — pick from index 0 forward.
      setActiveGroupId(res.group?.id || null)
      updateLayout((prev) => {
        let next = prev
        for (const p of res.processes) {
          next = placeProcessInLayout(next, p.id)
        }
        return next
      })
      refreshGroups()
      fetchProcesses()
    } else {
      toast.error(`Bundle launch failed${target ? ` ("${target.name}")` : ''}`)
    }
  }, [bundles, fetchProcesses, refreshGroups, toast, updateLayout])

  const refreshAudit = useCallback(async () => {
    setAuditEvents(await listAudit(200))
  }, [])

  const openAudit = useCallback(async () => {
    await refreshAudit()
    setAuditOpen(true)
  }, [refreshAudit])

  // Shareable URL: /agent-portal?preset=<id> or ?bundle=<id>. The
  // server validates auth + ownership at launch, not at parse, so it's
  // safe to act on the param without a pre-check round trip.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const bundle = params.get('bundle')
    const preset = params.get('preset')
    if (bundle) {
      // Wait until the bundle list has been fetched at least once so we
      // can use the friendly toast text.
      handleLaunchBundle(bundle)
      // Clear the param so a refresh doesn't re-launch.
      params.delete('bundle')
      const search = params.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${search ? '?' + search : ''}`)
    } else if (preset) {
      // Hand off to the existing preset-load path: open the launch
      // modal pre-populated with the preset.
      const cfg = launchConfigs.find((c) => c.id === preset)
      if (cfg) {
        applyEntry(cfg, { asPreset: true })
        setLaunchModalOpen(true)
      }
      params.delete('preset')
      const search = params.toString()
      window.history.replaceState({}, '', `${window.location.pathname}${search ? '?' + search : ''}`)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundles])

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
      if (activeGroupId) body.group_id = activeGroupId
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

  const cancelProcessById = useCallback(async (processId) => {
    if (!processId) return
    try {
      const res = await fetch(`/api/agent-portal/processes/${processId}`, {
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
  }, [fetchProcesses])

  const handleCancel = async () => cancelProcessById(focusedProcessId)

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

  // ------------------------------------------------------------------
  // Command palette actions
  // ------------------------------------------------------------------
  // Each action is a flat record so cmdk can fuzzy-match across title
  // and hint. Built in useMemo so the `when` predicates and `run`
  // closures see the latest state every render.

  const paletteActions = useMemo(() => {
    const acts = []
    acts.push({
      id: 'launch.new',
      title: 'New launch…',
      hint: 'Open the launch form',
      scope: 'Process',
      run: () => setLaunchModalOpen(true),
    })
    if (focusedProcessId) {
      acts.push({
        id: 'pane.cancel',
        title: 'Stop focused pane',
        hint: focusedProcess?.command || '',
        scope: 'Process',
        run: () => handleCancel(),
        when: () => !!focusedProcess && focusedProcess.status === 'running',
      })
      acts.push({
        id: 'pane.rename',
        title: 'Rename focused pane…',
        scope: 'Process',
        run: async () => {
          const answer = await dialog.prompt({
            title: 'Rename pane',
            label: 'Display name',
            defaultValue: focusedProcess?.display_name || focusedProcess?.command || '',
            okText: 'Rename',
          })
          if (answer && focusedProcessId) {
            await renameProcess(focusedProcessId, (answer.value || '').trim())
          }
        },
      })
      acts.push({
        id: 'pane.close',
        title: 'Remove focused pane from layout',
        hint: 'Process keeps running',
        scope: 'Process',
        run: () => handleCloseSlot(focusedSlot),
      })
      acts.push({
        id: 'pane.fullscreen',
        title: fullscreenSlot === focusedSlot ? 'Exit fullscreen' : 'Toggle fullscreen (F)',
        scope: 'Process',
        run: () => handleFullscreenSlot(focusedSlot),
      })
    }
    // Per-process cancel: lets the user kill any running process by name
    // without having to focus its slot first. Most-discoverable path for
    // "I'm done with that, just kill it."
    for (const p of processes) {
      if (p.status !== 'running') continue
      const label = p.display_name?.trim()
        || `${p.command} ${(p.args || []).join(' ')}`.trim()
      acts.push({
        id: `process.cancel.${p.id}`,
        title: `Stop: ${label}`,
        hint: `pid ${p.pid || '-'}`,
        scope: 'Process',
        run: () => cancelProcessById(p.id),
      })
    }
    for (const m of LAYOUT_MODES) {
      acts.push({
        id: `layout.mode.${m}`,
        title: `Layout: ${m}`,
        hint: m === layout.mode ? 'current' : '',
        scope: 'Layout',
        run: () => updateLayout((prev) => setLayoutMode(prev, m)),
        when: () => m !== layout.mode,
      })
    }
    for (let i = 0; i < layout.slots.length; i++) {
      const slotIdx = i
      acts.push({
        id: `layout.jump.${slotIdx}`,
        title: `Switch to pane ${slotIdx + 1}`,
        hint: layout.slots[slotIdx]
          ? processesById[layout.slots[slotIdx]]?.display_name
            || processesById[layout.slots[slotIdx]]?.command
            || ''
          : 'empty',
        scope: 'Layout',
        run: () => setFocusedSlot(slotIdx),
      })
    }
    if (focusedProcessId) {
      for (let i = 0; i < layout.slots.length; i++) {
        if (i === focusedSlot) continue
        const slotIdx = i
        acts.push({
          id: `layout.move.${slotIdx}`,
          title: `Move focused pane to slot ${slotIdx + 1}`,
          scope: 'Layout',
          run: () => updateLayout((prev) => moveProcessToSlot(prev, focusedProcessId, slotIdx)),
        })
      }
    }
    // Group actions (Phase 3).
    acts.push({
      id: 'group.create',
      title: 'New group…',
      scope: 'Group',
      run: () => handleCreateGroup(),
    })
    acts.push({
      id: 'group.clear-active',
      title: 'Clear active group (launch ungrouped)',
      hint: activeGroupId ? '' : 'already cleared',
      scope: 'Group',
      run: () => setActiveGroupId(null),
      when: () => !!activeGroupId,
    })
    for (const g of groups) {
      acts.push({
        id: `group.activate.${g.id}`,
        title: `Set active group: ${g.name}`,
        hint: activeGroupId === g.id ? 'current' : '',
        scope: 'Group',
        run: () => setActiveGroupId(g.id),
        when: () => activeGroupId !== g.id,
      })
      acts.push({
        id: `group.cancel.${g.id}`,
        title: `Cancel all panes in group: ${g.name}`,
        scope: 'Group',
        run: () => handleCancelGroup(g.id),
      })
      acts.push({
        id: `group.delete.${g.id}`,
        title: `Delete group: ${g.name}`,
        scope: 'Group',
        run: () => handleDeleteGroup(g.id),
      })
    }
    // Per-group sync toggles (Phase 5) + pause/snapshot (Phase 6).
    for (const g of groups) {
      const synced = syncedGroupIds.has(g.id)
      acts.push({
        id: `group.sync.${g.id}`,
        title: synced
          ? `Stop synchronize-input: ${g.name}`
          : `Synchronize input: ${g.name}`,
        scope: 'Group',
        run: () => toggleGroupSync(g.id),
      })
      acts.push({
        id: `group.pause.${g.id}`,
        title: `Pause group: ${g.name}`,
        hint: 'SIGSTOP every member',
        scope: 'Group',
        run: () => handlePauseGroup(g.id),
      })
      acts.push({
        id: `group.resume.${g.id}`,
        title: `Resume group: ${g.name}`,
        hint: 'SIGCONT every member',
        scope: 'Group',
        run: () => handleResumeGroup(g.id),
      })
      acts.push({
        id: `group.snapshot.${g.id}`,
        title: `Snapshot group: ${g.name}`,
        hint: 'Download scrollback as JSON',
        scope: 'Group',
        run: () => handleSnapshotGroup(g.id),
      })
    }
    acts.push({
      id: 'audit.open',
      title: 'Open audit log',
      scope: 'Global',
      run: () => openAudit(),
    })
    for (const b of bundles) {
      acts.push({
        id: `bundle.launch.${b.id}`,
        title: `Launch bundle: ${b.name}`,
        hint: `${(b.members || []).length} member(s)`,
        scope: 'Process',
        run: () => handleLaunchBundle(b.id),
      })
    }
    if (focusedProcess && focusedProcess.status !== 'running') {
      // "What ran here last" — re-launch.
      acts.push({
        id: 'pane.relaunch',
        title: 'Re-launch the exited pane',
        hint: focusedProcess.command || '',
        scope: 'Process',
        run: async () => {
          const body = {
            command: focusedProcess.command,
            args: focusedProcess.args || [],
            sandbox_mode: focusedProcess.sandbox_mode || 'off',
            extra_writable_paths: focusedProcess.extra_writable_paths || [],
            use_pty: !!focusedProcess.use_pty,
            namespaces: !!focusedProcess.namespaces,
            isolate_network: !!focusedProcess.isolate_network,
            display_name: focusedProcess.display_name || '',
          }
          if (focusedProcess.cwd) body.cwd = focusedProcess.cwd
          if (focusedProcess.memory_limit) body.memory_limit = focusedProcess.memory_limit
          if (focusedProcess.cpu_limit) body.cpu_limit = focusedProcess.cpu_limit
          if (focusedProcess.pids_limit) body.pids_limit = focusedProcess.pids_limit
          if (focusedProcess.group_id) body.group_id = focusedProcess.group_id
          try {
            const res = await fetch('/api/agent-portal/processes', {
              method: 'POST',
              credentials: 'include',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            })
            if (res.ok) {
              const proc = await res.json()
              updateLayout((prev) => placeProcessInLayout(prev, proc.id, focusedSlot))
              fetchProcesses()
              toast.success('Re-launched.')
            } else {
              const err = await res.json().catch(() => ({}))
              toast.error(err.detail || `Re-launch failed (${res.status})`)
            }
          } catch (e) {
            toast.error(e.message || 'Re-launch failed')
          }
        },
      })
    }
    acts.push({
      id: 'global.refresh',
      title: 'Refresh process list',
      scope: 'Global',
      run: () => fetchProcesses(),
    })
    acts.push({
      id: 'global.toggle-panel',
      title: leftCollapsed ? 'Show launcher panel' : 'Hide launcher panel',
      scope: 'Global',
      run: () => setLeftCollapsed((v) => !v),
    })
    acts.push({
      id: 'preset.save',
      title: 'Save current launch as preset…',
      scope: 'Global',
      run: () => saveCurrentAsPreset(),
      when: () => !!command.trim(),
    })
    return acts
  }, [
    focusedProcess,
    focusedProcessId,
    focusedSlot,
    fullscreenSlot,
    layout,
    processes,
    processesById,
    cancelProcessById,
    leftCollapsed,
    command,
    dialog,
    fetchProcesses,
    handleCancel,
    handleCloseSlot,
    handleFullscreenSlot,
    renameProcess,
    saveCurrentAsPreset,
    toast,
    updateLayout,
    groups,
    activeGroupId,
    handleCreateGroup,
    handleCancelGroup,
    handleDeleteGroup,
    bundles,
    handleLaunchBundle,
    openAudit,
    syncedGroupIds,
    toggleGroupSync,
    handlePauseGroup,
    handleResumeGroup,
    handleSnapshotGroup,
  ])

  // Keyboard shortcuts.
  //   Ctrl-Shift-P     → open the command palette (always — it's the
  //                      one binding that wins over an active xterm so
  //                      the user is never trapped without a way out).
  //   Esc              → close palette / exit fullscreen.
  //   F                → toggle fullscreen for the focused slot.
  //   1-9              → jump to slot N (gated on activeElement so
  //                      terminal input still wins).
  //   Ctrl-Shift-←/→/↑/↓ → move focus between panes.
  //
  // The terminal-friendly bindings (F, 1-9, arrows) bail when an
  // input, textarea, contenteditable, or xterm has focus, so they
  // never eat user keystrokes mid-edit.
  useEffect(() => {
    const handler = (e) => {
      // Ctrl-Shift-P: highest priority; works even with a terminal focused.
      if (e.key === 'P' && e.ctrlKey && e.shiftKey) {
        setPaletteOpen((v) => !v)
        e.preventDefault()
        return
      }

      const ae = document.activeElement
      const tag = ae?.tagName
      const inEdit = tag === 'INPUT' || tag === 'TEXTAREA' || ae?.isContentEditable
      const inXterm = !!ae?.closest?.('[data-testid="agent-portal-pane"] .xterm')

      if (e.key === 'Escape') {
        if (paletteOpen) {
          setPaletteOpen(false)
          e.preventDefault()
        } else if (fullscreenSlot != null) {
          setFullscreenSlot(null)
          e.preventDefault()
        }
        return
      }

      if (inEdit || inXterm) return

      // F → fullscreen toggle.
      if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        if (layout.slots[focusedSlot]) {
          setFullscreenSlot((prev) => (prev === focusedSlot ? null : focusedSlot))
          e.preventDefault()
        }
        return
      }

      // 1-9 → jump to that slot.
      if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const target = parseInt(e.key, 10) - 1
        if (target >= 0 && target < layout.slots.length) {
          setFocusedSlot(target)
          e.preventDefault()
        }
        return
      }

      // Ctrl-Shift-Arrow → move focus.
      if (e.ctrlKey && e.shiftKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        const slots = layout.slots
        if (slots.length === 0) return
        let next = focusedSlot
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (focusedSlot + 1) % slots.length
        else next = (focusedSlot - 1 + slots.length) % slots.length
        setFocusedSlot(next)
        e.preventDefault()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [paletteOpen, fullscreenSlot, focusedSlot, layout])

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
            <Square className="w-4 h-4" /> <span className="hidden sm:inline">Stop</span>
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
            onClick={() => setPaletteOpen(true)}
            className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm"
            title="Command palette (Ctrl-Shift-P)"
          >
            <span className="hidden sm:inline">Commands</span>
            <kbd className="hidden md:inline text-[10px] bg-gray-900 border border-gray-600 rounded px-1.5 py-0.5">Ctrl+Shift+P</kbd>
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
            <div className="flex items-center justify-between text-xs uppercase text-gray-400 mb-1">
              <span>Groups</span>
              <button
                type="button"
                onClick={handleCreateGroup}
                className="text-blue-300 hover:text-blue-200 normal-case"
                title="Create a new group"
              >
                + New
              </button>
            </div>
            {groups.length === 0 ? (
              <div className="text-[11px] text-gray-500">
                No groups yet. Groups give panes a shared budget and a one-click cancel.
              </div>
            ) : (
              <div className="space-y-1">
                <button
                  type="button"
                  onClick={() => setActiveGroupId(null)}
                  className={`w-full text-left text-xs px-2 py-1 rounded border ${
                    activeGroupId == null
                      ? 'bg-gray-700 border-blue-500 text-gray-100'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:bg-gray-700'
                  }`}
                >
                  (no group)
                </button>
                {groups.map((g) => {
                  const live = processes.filter((p) => p.group_id === g.id && p.status === 'running').length
                  const cap = g.max_panes
                  const synced = syncedGroupIds.has(g.id)
                  return (
                    <div
                      key={g.id}
                      className={`flex items-center gap-1 text-xs rounded px-2 py-1 border ${
                        synced
                          ? 'bg-amber-900/40 border-amber-500'
                          : activeGroupId === g.id
                          ? 'bg-gray-700 border-blue-500'
                          : 'bg-gray-800 border-gray-700'
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => setActiveGroupId(g.id)}
                        className="flex-1 min-w-0 text-left truncate text-gray-100"
                        title={`Activate group "${g.name}"`}
                      >
                        <span className="font-medium">{g.name}</span>
                        <span className="ml-1 text-gray-500 text-[10px]">
                          {live}{cap ? `/${cap}` : ''}
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleGroupSync(g.id)}
                        className={`p-0.5 flex-shrink-0 ${
                          synced ? 'text-amber-300' : 'text-gray-500 hover:text-amber-300'
                        }`}
                        title={synced
                          ? 'Sync ON — keystrokes fan out to every PTY pane in this group'
                          : 'Toggle synchronize-input for this group'}
                      >
                        {synced ? '⏵⏵' : '⏵'}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleCancelGroup(g.id)}
                        className="p-0.5 text-gray-500 hover:text-yellow-300 flex-shrink-0"
                        title="Cancel all panes in this group"
                      >
                        <Square className="w-3 h-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteGroup(g.id)}
                        className="p-0.5 text-gray-500 hover:text-red-400 flex-shrink-0"
                        title="Delete group (and reap members)"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
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
                autoFocus
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
              onCancelProcess={cancelProcessById}
              onFullscreenSlot={handleFullscreenSlot}
              onRenameProcess={renameProcess}
              onProcessUpdate={handleProcessUpdate}
              syncedGroupIds={Array.from(syncedGroupIds)}
            />
          </div>
        </div>
      </div>
      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        actions={paletteActions}
      />
      {auditOpen && (
        <div
          className="fixed inset-0 z-[10001] bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => setAuditOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label="Audit log"
        >
          <div
            className="w-full max-w-4xl max-h-[80vh] flex flex-col bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700">
              <h2 className="text-lg font-semibold text-gray-100">Audit log</h2>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={refreshAudit}
                  className="p-1.5 text-gray-400 hover:text-blue-300"
                  title="Refresh"
                >
                  <RefreshCw className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setAuditOpen(false)}
                  className="p-1.5 text-gray-400 hover:text-gray-200"
                  aria-label="Close"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto font-mono text-xs">
              {auditEvents.length === 0 ? (
                <div className="p-4 text-gray-500">No audit events yet.</div>
              ) : (
                <table className="w-full">
                  <thead className="text-[10px] uppercase text-gray-500 sticky top-0 bg-gray-900">
                    <tr>
                      <th className="text-left px-3 py-2">Timestamp</th>
                      <th className="text-left px-3 py-2">Event</th>
                      <th className="text-left px-3 py-2">Group</th>
                      <th className="text-left px-3 py-2">Process</th>
                      <th className="text-left px-3 py-2">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditEvents.map((e) => (
                      <tr key={e.id} className="border-t border-gray-800">
                        <td className="px-3 py-1.5 text-gray-400 whitespace-nowrap">{e.ts?.replace('T', ' ').slice(0, 19)}</td>
                        <td className="px-3 py-1.5 text-gray-200">{e.event}</td>
                        <td className="px-3 py-1.5 text-gray-500">{e.group_id ? e.group_id.slice(0, 8) : '—'}</td>
                        <td className="px-3 py-1.5 text-gray-500">{e.process_id ? e.process_id.slice(0, 8) : '—'}</td>
                        <td className="px-3 py-1.5 text-gray-400 truncate max-w-md">
                          {e.detail ? JSON.stringify(e.detail) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
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
