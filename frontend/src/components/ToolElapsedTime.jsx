import { useState, useEffect, useRef } from 'react'
import { SLEEP_TOOL, migrateToolName } from '../constants/atlasTools'

// Live "Xs" / "Xm Ys" ticker shown next to an active tool call. Once the tool
// has been running past TOOL_SLOW_THRESHOLD_SEC we append a warning message.
//
// For the built-in sleep tool we instead show a progress clock against the requested
// duration (`MM:SS of MM:SS`), because the generic slow-tool warning is
// meaningless for a tool whose job is to wait minutes or hours (#838). Once the
// requested wait has elapsed we switch to a "completing..." hint — the backend
// is wrapping up and the tool_complete frame has not arrived yet.
//
// Extracted from Message.jsx. The 1-second setInterval is intentional — this
// is a UI ticker, not a polling loop. See AGENTS.md on Message.jsx polling
// rules.
const TOOL_SLOW_THRESHOLD_SEC = 30

// Clock style `MM:SS` (or `HH:MM:SS` for waits of an hour or more) — matches
// the format the issue asked for and stays compact for the long waits the
// sleep tool exists for. `useHours` lets the caller keep both the elapsed and
// requested values on the same clock style even when only one of them has
// crossed the hour boundary.
const formatClock = (totalSec, useHours = false) => {
  const s = Math.max(0, Math.floor(totalSec))
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60
  const mm = String(minutes).padStart(2, '0')
  const ss = String(seconds).padStart(2, '0')
  return (useHours || hours > 0) ? `${String(hours).padStart(2, '0')}:${mm}:${ss}` : `${mm}:${ss}`
}

const ToolElapsedTime = ({ timestamp, toolName, arguments: args }) => {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(timestamp ? new Date(timestamp).getTime() : Date.now())

  useEffect(() => {
    startRef.current = timestamp ? new Date(timestamp).getTime() : Date.now()
    setElapsed(0)
  }, [timestamp])

  useEffect(() => {
    const tick = () => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  // Sleep tool: show progress against the requested duration instead of the
  // generic "taking longer than expected" warning, which is misleading for a
  // tool whose purpose is to wait minutes or hours (#838). A non-numeric or
  // non-positive `seconds` falls back to the generic timer so we never show a
  // nonsense "of NaN:NaN".
  const rawSeconds = (args && args.seconds != null) ? args.seconds : null
  const sleepSeconds = (
    migrateToolName(toolName) === SLEEP_TOOL &&
    rawSeconds != null &&
    Number.isFinite(Number(rawSeconds)) &&
    Number(rawSeconds) > 0
  ) ? Number(rawSeconds) : null

  if (sleepSeconds != null) {
    const overdue = elapsed >= sleepSeconds
    // Keep both numbers on the same clock style: once either side crosses the
    // hour boundary, render both as HH:MM:SS so the elapsed / requested pair
    // never reads like `01:00 of 2:00:00`.
    const useHours = sleepSeconds >= 3600 || elapsed >= 3600
    return (
      <span className="flex items-center gap-1 text-xs text-gray-400 ml-1">
        <span>{formatClock(elapsed, useHours)} of {formatClock(sleepSeconds, useHours)}</span>
        {overdue && (
          <span className="text-yellow-400">- completing...</span>
        )}
      </span>
    )
  }

  const minutes = Math.floor(elapsed / 60)
  const seconds = elapsed % 60
  const timeStr = minutes > 0
    ? `${minutes}m ${String(seconds).padStart(2, '0')}s`
    : `${seconds}s`
  const isSlow = elapsed >= TOOL_SLOW_THRESHOLD_SEC

  return (
    <span className="flex items-center gap-1 text-xs text-gray-400 ml-1">
      <span>{timeStr}</span>
      {isSlow && (
        <span className="text-yellow-400">- taking longer than expected</span>
      )}
    </span>
  )
}

export default ToolElapsedTime
