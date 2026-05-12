// A single multi-pane cell. Owns its own WebSocket to the process
// stream endpoint, its own xterm.js / scrollback ring buffer, and its
// own ResizeObserver+FitAddon. Lives forever for the lifetime of the
// slot it occupies — moving a process between slots remounts the
// underlying xterm by design (slot-keyed), but switching the process
// inside one slot just rebinds the WS without recreating xterm.

import { useEffect, useMemo, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { Maximize2, X, Edit2, Check, Shield, Boxes, Square, Trash2 } from 'lucide-react'

// Plain-text scrollback ring buffer for non-PTY processes. Keep the
// last N chunks so swapping between slots in 2x2 view doesn't lose
// context. PTY processes use xterm's own scrollback (5000 lines).
const TEXT_SCROLLBACK_MAX = 1000

const STREAM_COLORS = {
  stdout: 'text-gray-200',
  stderr: 'text-red-300',
  system: 'text-blue-300 italic',
}

/**
 * Pane - one cell in the multi-pane grid.
 *
 * Props:
 *   process            - server-provided summary (id, command, args, ...)
 *                        or null when the slot is empty.
 *   onClose            - () => void; called when the user evicts this slot.
 *   onFullscreen       - () => void; called when the user toggles fullscreen.
 *   onRename           - (id, newName) => void
 *   isFullscreen       - boolean; suppresses the fullscreen affordance.
 *   isFocused          - boolean; visual ring around the focused pane.
 *   onFocus            - () => void; called when the user clicks into the pane.
 *   onProcessUpdate    - (summary) => void; raised on process_info / process_end
 *                        so the parent can keep its process list fresh.
 *   syncEnabled        - boolean; when true, stdin from this pane fans out
 *                        to every PTY-backed member of the same group via
 *                        the WS broadcast flag (server enforces).
 */
function Pane({
  process,
  onClose,
  onCancel,
  onRemove,
  onFullscreen,
  onRename,
  isFullscreen,
  isFocused,
  onFocus,
  onProcessUpdate,
  syncEnabled,
}) {
  // Keep the latest sync flag in a ref so the xterm onData callback
  // always reads the current value without re-creating the disposable.
  const syncRef = useRef(!!syncEnabled)
  useEffect(() => { syncRef.current = !!syncEnabled }, [syncEnabled])
  const wsRef = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const hostRef = useRef(null)
  // Buffer raw chunks that arrive on the WS before xterm has mounted
  // (history replay races with xterm initialization on a fast-arriving
  // stream — same race the original AgentPortal addressed).
  const pendingRawRef = useRef([])
  // Same race for system messages — surface them in xterm once it mounts.
  const pendingSysRef = useRef([])
  const [chunks, setChunks] = useState([])
  // Latest system message kept for the banner under the breadcrumb.
  // System messages tell the user about silence watchdogs, sandbox
  // setup failures, and exit codes — without this, PTY-mode panes
  // would hide them entirely since the body is xterm, not <chunks>.
  const [lastSystemMsg, setLastSystemMsg] = useState(null)
  const [editingName, setEditingName] = useState(false)
  const [draftName, setDraftName] = useState(process?.display_name || '')
  const [connected, setConnected] = useState(false)

  // ---- xterm lifecycle (PTY processes only) ------------------------------
  // Slot-keyed remount: when process.id changes the parent does NOT
  // unmount Pane (slot key stays the same), so we tear down + reopen
  // xterm here based on use_pty + id.

  const isPty = !!process?.use_pty
  const procId = process?.id || null

  useEffect(() => {
    if (!isPty || !hostRef.current || !procId) return
    const term = new XTerm({
      convertEol: false,
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, monospace',
      fontSize: 13,
      theme: { background: '#000000', foreground: '#e5e7eb' },
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
    const onDataDisp = term.onData((data) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== 1) return
      const bytes = new TextEncoder().encode(data)
      let bin = ''
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
      // broadcast flag fans this stdin to every PTY-backed member of
      // the focused process's group (server enforces).
      const payload = { type: 'input', data: btoa(bin) }
      if (syncRef.current) payload.broadcast = true
      ws.send(JSON.stringify(payload))
    })
    const onResizeDisp = term.onResize(sendResize)
    // Debounce fit + SIGWINCH: a layout-mode change fires a burst of
    // ResizeObserver callbacks during the CSS grid transition. Refitting
    // and resizing the PTY on each intermediate size leaves the inner
    // program's buffer out of sync with the final cell size, which
    // surfaces as mangled / overlapping text. Coalesce to a single fit
    // after the transition has settled.
    let fitTimer = null
    const doFit = () => {
      if (fitTimer) clearTimeout(fitTimer)
      fitTimer = setTimeout(() => {
        fitTimer = null
        try { fit.fit() } catch { /* ignore */ }
        sendResize()
      }, 120)
    }

    // Wait one frame so the grid template has settled before the first
    // fit; otherwise the row count comes in as the placeholder default
    // and the next true resize garbles the buffer.
    const raf = requestAnimationFrame(() => {
      try { fit.fit() } catch { /* ignore */ }
      sendResize()
    })

    const ro = new ResizeObserver(doFit)
    ro.observe(hostRef.current)
    window.addEventListener('resize', doFit)

    // Drain any chunks the WS buffered before xterm existed.
    if (pendingRawRef.current.length > 0) {
      for (const data of pendingRawRef.current) writePtyChunk(term, data)
      pendingRawRef.current = []
    }
    if (pendingSysRef.current.length > 0) {
      for (const text of pendingSysRef.current) writeSystemMessage(term, text)
      pendingSysRef.current = []
    }

    return () => {
      cancelAnimationFrame(raf)
      if (fitTimer) clearTimeout(fitTimer)
      ro.disconnect()
      window.removeEventListener('resize', doFit)
      onDataDisp.dispose()
      onResizeDisp.dispose()
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
  }, [isPty, procId])

  // ---- WebSocket lifecycle ----------------------------------------------

  useEffect(() => {
    if (!procId) {
      setChunks([])
      pendingRawRef.current = []
      pendingSysRef.current = []
      setLastSystemMsg(null)
      return
    }
    if (wsRef.current) {
      try { wsRef.current.close() } catch { /* ignore */ }
      wsRef.current = null
    }
    setChunks([])
    pendingRawRef.current = []
    pendingSysRef.current = []
    setLastSystemMsg(null)
    if (termRef.current) termRef.current.clear()

    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${window.location.host}/api/agent-portal/processes/${procId}/stream`
    const ws = new WebSocket(url)
    wsRef.current = ws
    setConnected(false)

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onerror = () => setConnected(false)

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'process_info' || msg.type === 'process_end') {
          if (onProcessUpdate) onProcessUpdate(msg.process)
        } else if (msg.type === 'output') {
          setChunks((prev) => {
            const next = prev.length >= TEXT_SCROLLBACK_MAX
              ? prev.slice(prev.length - TEXT_SCROLLBACK_MAX + 1)
              : prev
            return [...next, { stream: msg.stream, text: msg.text, timestamp: msg.timestamp }]
          })
          // System messages (launch banner, silence watchdog, exit code,
          // sandbox-setup failures) need to be visible regardless of
          // mode. In PTY mode the pane body is xterm, so chunks above
          // are invisible — write the message into xterm too, and pin
          // the latest one in a banner under the breadcrumb so users
          // see exit / failure status without scrolling the terminal.
          if (msg.stream === 'system') {
            setLastSystemMsg({ text: msg.text || '', timestamp: msg.timestamp })
            const term = termRef.current
            if (term) writeSystemMessage(term, msg.text || '')
            else pendingSysRef.current.push(msg.text || '')
          }
        } else if (msg.type === 'output_raw') {
          const term = termRef.current
          if (term) writePtyChunk(term, msg.data)
          else pendingRawRef.current.push(msg.data)
        }
      } catch {
        // Ignore parse errors — same forgiving behavior as the original.
      }
    }

    return () => {
      try { ws.close() } catch { /* ignore */ }
      wsRef.current = null
    }
    // onProcessUpdate is intentionally omitted from deps — it is a
    // useCallback in the parent and re-creating the WS on every parent
    // render would thrash live streams.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [procId])

  // Keep xterm fitted after the grid template changes (entering /
  // exiting fullscreen, swapping layout modes, etc.) — the parent
  // bumps a version number and we do a fresh fit on each tick.
  // ResizeObserver already covers most cases, but xterm's first paint
  // can lock in the wrong row count if the container resizes mid-mount.

  const scrollRef = useRef(null)
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [chunks])

  // Inline hint strip: show after 5s of no output, hide on next chunk.
  // Helps users discover Ctrl-Shift-P / F / 1-9 without reading docs.
  const [showHint, setShowHint] = useState(false)
  useEffect(() => {
    setShowHint(false)
    if (!process || !procId) return
    const t = setTimeout(() => setShowHint(true), 5000)
    return () => clearTimeout(t)
  }, [process, procId, chunks.length])

  const headerLabel = useMemo(() => {
    if (!process) return 'Empty pane'
    return process.display_name?.trim()
      || `${process.command}${process.args?.length ? ' ' + process.args.join(' ') : ''}`
  }, [process])

  // Per-pane breadcrumb (Phase 6 polish): cwd · sandbox · group.
  // Server supplies all three as plain strings so a future remote
  // executor doesn't have to map host paths into browser URLs.
  const breadcrumb = useMemo(() => {
    if (!process) return null
    const parts = []
    if (process.cwd) parts.push(process.cwd)
    if (process.sandbox_mode && process.sandbox_mode !== 'off') parts.push(`sandbox:${process.sandbox_mode}`)
    if (process.group_id) parts.push(`group:${process.group_id.slice(0, 8)}`)
    return parts.join(' · ')
  }, [process])

  const commitName = () => {
    setEditingName(false)
    const next = draftName.trim()
    if (next !== (process?.display_name || '').trim()) {
      onRename?.(procId, next)
    }
  }

  const statusLabel = process?.status || ''
  const statusBadge = statusLabel === 'running'
    ? 'bg-green-900 text-green-300 border-green-700'
    : statusLabel === 'failed'
    ? 'bg-red-900 text-red-300 border-red-700'
    : statusLabel === 'cancelled'
    ? 'bg-yellow-900 text-yellow-300 border-yellow-700'
    : 'bg-gray-800 text-gray-300 border-gray-700'

  return (
    <div
      onClick={() => onFocus?.()}
      className={`flex-1 flex flex-col min-h-0 min-w-0 bg-gray-900 border-2 rounded-lg overflow-hidden ${
        // Sync wins over focus visually so the user is never confused
        // about which panes will receive their next keystroke. Don't
        // repeat tmux's silent synchronize-panes footgun.
        syncEnabled
          ? 'border-amber-500 ring-1 ring-amber-500/40'
          : isFocused
          ? 'border-blue-500 ring-1 ring-blue-500/40'
          : 'border-gray-700'
      }`}
      data-testid="agent-portal-pane"
    >
      <div className="flex items-center gap-1 px-2 py-1 border-b border-gray-700 bg-gray-800 text-xs">
        {process?.sandboxed && (
          <Shield className="w-3 h-3 text-blue-400 flex-shrink-0" title="Sandboxed (Landlock)" />
        )}
        {process?.namespaces && (
          <Boxes className="w-3 h-3 text-purple-400 flex-shrink-0" title="Isolated namespaces" />
        )}
        {editingName ? (
          <input
            autoFocus
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onBlur={commitName}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitName()
              else if (e.key === 'Escape') {
                setEditingName(false)
                setDraftName(process?.display_name || '')
              }
            }}
            className="flex-1 min-w-0 bg-gray-900 border border-gray-600 rounded px-1.5 py-0.5 text-xs"
          />
        ) : (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); if (procId) setEditingName(true) }}
            className="flex-1 min-w-0 text-left truncate text-gray-100 font-medium"
            title={headerLabel}
            disabled={!procId}
          >
            {headerLabel}
          </button>
        )}
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${statusBadge}`}>
          {statusLabel || '—'}
        </span>
        {procId && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setEditingName((v) => !v) }}
            className="p-1 text-gray-500 hover:text-blue-300"
            title={editingName ? 'Save name' : 'Rename'}
          >
            {editingName ? <Check className="w-3 h-3" /> : <Edit2 className="w-3 h-3" />}
          </button>
        )}
        {procId && onCancel && process?.status === 'running' && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onCancel() }}
            className="p-1 text-gray-500 hover:text-red-400"
            title="Stop this process (SIGTERM)"
          >
            <Square className="w-3 h-3" />
          </button>
        )}
        {procId && onRemove && process && process.status !== 'running' && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onRemove(process) }}
            className="p-1 text-gray-500 hover:text-red-400"
            title="Remove from list (process is finished)"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        )}
        {procId && onFullscreen && !isFullscreen && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onFullscreen() }}
            className="p-1 text-gray-500 hover:text-blue-300"
            title="Fullscreen this pane (F)"
          >
            <Maximize2 className="w-3 h-3" />
          </button>
        )}
        {procId && onClose && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onClose() }}
            className="p-1 text-gray-500 hover:text-red-400"
            title="Remove from layout (process keeps running)"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>

      {breadcrumb && (
        <div className="px-2 py-0.5 border-b border-gray-800 bg-gray-900 text-[10px] text-gray-500 font-mono truncate">
          {breadcrumb}
        </div>
      )}

      {lastSystemMsg && (
        <div
          className="px-2 py-1 border-b border-yellow-900/60 bg-yellow-950/40 text-[11px] text-yellow-200 font-mono whitespace-pre-wrap"
          data-testid="agent-portal-system-msg"
          title="Latest system message"
        >
          {lastSystemMsg.text}
        </div>
      )}

      {/* Body */}
      {!process ? (
        <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
          Empty slot
        </div>
      ) : isPty ? (
        <div className="flex-1 min-h-0 w-full bg-black overflow-hidden p-1">
          <div ref={hostRef} className="h-full w-full" />
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-2 bg-black font-mono text-xs whitespace-pre-wrap break-words"
        >
          {chunks.length === 0 ? (
            <div className="text-gray-500">{connected ? 'Waiting for output...' : 'Connecting...'}</div>
          ) : (
            chunks.map((c, i) => (
              <div key={i} className={STREAM_COLORS[c.stream] || STREAM_COLORS.stdout}>
                {c.text}
              </div>
            ))
          )}
        </div>
      )}
      {showHint && process && (
        <div className="px-2 py-0.5 bg-gray-900/80 text-[10px] text-gray-500 font-mono border-t border-gray-800 truncate">
          Ctrl-Shift-P · F fullscreen · 1-9 jump
        </div>
      )}
    </div>
  )
}

function writePtyChunk(term, base64Data) {
  try {
    const bin = atob(base64Data)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    term.write(bytes)
  } catch {
    // Defensive: drop malformed chunks rather than crashing the pane.
  }
}

// Render a system message into an xterm pane. Multi-line messages
// get a yellow [atlas] prefix on each line so a Landlock denial,
// silence-watchdog hint, or exit-code banner is impossible to miss
// when it lands in the middle of the program's own output.
function writeSystemMessage(term, text) {
  try {
    const lines = (text || '').split('\n')
    for (const line of lines) {
      // ANSI: bold yellow on default bg. Reset after each line so the
      // user's program keeps its own colors on subsequent output.
      term.write(`\r\n\x1b[1;33m${line}\x1b[0m\r\n`)
    }
  } catch {
    // Defensive: ignore if the terminal got disposed between checks.
  }
}

export default Pane
