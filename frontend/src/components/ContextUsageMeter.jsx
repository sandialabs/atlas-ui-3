import { useCallback, useEffect, useRef, useState } from 'react'
import { Gauge, Loader2 } from 'lucide-react'
import { FOCUS_RING } from '../utils/focusRing'
import { formatTokens, meterLevel, WINDOW_SOURCE_LABEL } from '../utils/contextUsage'

// Active-context meter and compaction controls (issue #877).
//
// Shows the server's estimate of the conversation's *next* request (system
// prompt, history after any checkpoint, tool definitions) against the model's
// context window. It is labelled as an estimate throughout, and kept apart
// from cumulative usage, which is what the provider billed across turns.

const LEVEL_BAR = {
  ok: 'bg-emerald-500',
  warn: 'bg-amber-400',
  high: 'bg-orange-500',
  over: 'bg-red-500',
}

const LEVEL_ICON = {
  ok: 'text-emerald-400',
  warn: 'text-amber-400',
  high: 'text-orange-400',
  over: 'text-red-400',
}

const ContextUsageMeter = ({ contextCompaction, disabled = false }) => {
  const [open, setOpen] = useState(false)
  const [focus, setFocus] = useState('')
  // Which edge of the button the details panel hangs from, so a meter near
  // the right of a narrow row does not paint the panel off screen.
  const [panelSide, setPanelSide] = useState('left')
  const panelRef = useRef(null)
  const buttonRef = useRef(null)
  const { usage, isCompacting, autoCompact, setAutoCompact, compactNow, enabled } = contextCompaction || {}

  const placePanel = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    const roomRight = window.innerWidth - rect.left
    const roomLeft = rect.right
    setPanelSide(roomRight >= roomLeft ? 'left' : 'right')
  }, [])

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
    const onClick = (e) => { if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false) }
    placePanel()
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    window.addEventListener('resize', placePanel)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
      window.removeEventListener('resize', placePanel)
    }
  }, [open, placePanel])

  if (!enabled || (!usage && !isCompacting)) return null

  const level = meterLevel(usage)
  const used = usage?.estimated_tokens
  const windowTotal = usage?.context_window
  const assumed = usage?.window_source === 'fallback'
  const percent = usage && windowTotal ? Math.min(100, Math.round((used / windowTotal) * 100)) : 0
  const label = usage
    ? `~${formatTokens(used)} / ${formatTokens(windowTotal)}${assumed ? '?' : ''}`
    : 'Compacting...'

  const submitCompact = (e) => {
    e.preventDefault()
    if (compactNow(focus.trim())) {
      setFocus('')
      setOpen(false)
    }
  }

  // The full "Context ~2K / 6K" readout shares the model/prompt row. Below
  // the sm breakpoint only the gauge and the estimate stay, so the chip
  // fits that row on a phone. The accessible name keeps the whole fraction.
  const fullLabel = `Context ${label}`
  const shortLabel = usage ? `~${formatTokens(used)}${assumed ? '?' : ''}` : '…'

  return (
    <div className="relative shrink-0" ref={panelRef}>
      <button
        type="button"
        ref={buttonRef}
        onClick={() => { placePanel(); setOpen(!open) }}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`${fullLabel}. Estimated context of the next request. Click for details and compaction.`}
        title="Estimated context of the next request. Click for details and compaction."
        className={`flex items-center gap-1.5 rounded px-2 py-1 text-xs text-gray-300 bg-gray-700/70 hover:bg-gray-600 whitespace-nowrap ${FOCUS_RING}`}
        data-testid="context-usage-meter"
      >
        {isCompacting
          ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
          : <Gauge className={`w-3.5 h-3.5 shrink-0 ${LEVEL_ICON[level]}`} aria-hidden="true" />}
        <span className="sm:hidden">{shortLabel}</span>
        <span className="hidden sm:inline">{fullLabel}</span>
        {usage && (
          <span className="hidden md:block w-12 h-1.5 rounded bg-gray-600 overflow-hidden shrink-0" aria-hidden="true">
            <span className={`block h-full ${LEVEL_BAR[level]}`} style={{ width: `${percent}%` }} />
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Context usage and compaction"
          className={`absolute bottom-full mb-2 w-80 max-w-[calc(100vw-2rem)] max-h-[60vh] overflow-y-auto rounded-lg border border-gray-600 bg-gray-800 p-3 text-xs text-gray-300 shadow-xl z-30 ${
            panelSide === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {usage ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-gray-400">Next request</dt>
              <dd>~{formatTokens(used)} tokens ({usage.estimate_method === 'tokenizer' ? 'tokenizer estimate' : 'rough estimate'})</dd>
              <dt className="text-gray-400">Context window</dt>
              <dd>{formatTokens(windowTotal)} ({WINDOW_SOURCE_LABEL[usage.window_source] || usage.window_source})</dd>
              <dt className="text-gray-400">Usable</dt>
              <dd>{formatTokens(usage.usable_tokens)} after reply reserve</dd>
              <dt className="text-gray-400">Auto-compacts at</dt>
              <dd>~{formatTokens(usage.threshold_tokens)}</dd>
              {typeof usage.provider_last_prompt_tokens === 'number' && (
                <>
                  <dt className="text-gray-400">Last request</dt>
                  <dd>{formatTokens(usage.provider_last_prompt_tokens)} (reported by the provider)</dd>
                </>
              )}
              {usage.reported_calls > 0 && (
                <>
                  <dt className="text-gray-400">Session usage</dt>
                  <dd>
                    {formatTokens(usage.cumulative_prompt_tokens)} in / {formatTokens(usage.cumulative_completion_tokens)} out
                    {' '}over {usage.reported_calls} calls (cumulative, provider-reported)
                  </dd>
                </>
              )}
              {usage.checkpoints > 0 && (
                <>
                  <dt className="text-gray-400">Compactions</dt>
                  <dd>{usage.checkpoints}</dd>
                </>
              )}
            </dl>
          ) : (
            <p>Compacting the conversation...</p>
          )}

          <label className="mt-3 flex items-center gap-2">
            <input
              type="checkbox"
              checked={!!autoCompact}
              onChange={(e) => setAutoCompact(e.target.checked)}
              className="accent-sky-500"
              data-testid="auto-compact-toggle"
            />
            <span>Compact automatically in this conversation</span>
          </label>

          <form onSubmit={submitCompact} className="mt-3 flex flex-col gap-2">
            <label htmlFor="compact-focus" className="text-gray-400">
              Compact now (optional focus, e.g. &quot;the auth design decisions&quot;)
            </label>
            <input
              id="compact-focus"
              type="text"
              value={focus}
              maxLength={1000}
              onChange={(e) => setFocus(e.target.value)}
              placeholder="What to keep in most detail"
              className="rounded bg-gray-900 border border-gray-600 px-2 py-1 text-gray-200"
            />
            <button
              type="submit"
              disabled={disabled || isCompacting}
              className={`self-end rounded px-3 py-1 bg-sky-700 hover:bg-sky-600 disabled:opacity-50 disabled:cursor-not-allowed text-white ${FOCUS_RING}`}
              data-testid="compact-now"
            >
              {isCompacting ? 'Compacting...' : 'Compact'}
            </button>
            <p className="text-gray-500">
              Older messages are summarized into a checkpoint; the full transcript is kept. You can also type /compact.
            </p>
          </form>
        </div>
      )}
    </div>
  )
}

export default ContextUsageMeter
