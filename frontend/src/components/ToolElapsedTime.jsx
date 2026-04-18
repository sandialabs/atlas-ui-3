import { useState, useEffect, useRef } from 'react'

// Live "Xs" / "Xm Ys" ticker shown next to an active tool call. Once the tool
// has been running past TOOL_SLOW_THRESHOLD_SEC we append a warning message.
//
// Extracted from Message.jsx. Behavior is unchanged from the inline version.
// The 1-second setInterval is intentional — this is a UI ticker, not a
// polling loop. See AGENTS.md on Message.jsx polling rules.
const TOOL_SLOW_THRESHOLD_SEC = 30

const ToolElapsedTime = ({ timestamp }) => {
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
