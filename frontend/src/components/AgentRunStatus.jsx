import { Sparkles, Square } from 'lucide-react'

/**
 * Persistent "agent is running" status pinned to the composer.
 *
 * The transcript's busy row (AgentBusyIndicator) only exists while the user is
 * scrolled to the newest message, and it deliberately disappears whenever
 * tokens are streaming, so a long agent run can look idle the moment someone
 * scrolls up to read earlier output. This row lives in the footer instead: it
 * is above the composer, always on screen, and carries its own Stop so the run
 * is both visible and interruptible from anywhere in the transcript.
 *
 * It reads the same run state as the composer's Stop button (`isAgentRunning`,
 * `currentAgentStep`, `stopAgent` from ChatContext) -- there is no second
 * source of truth for whether a run is active.
 */
export default function AgentRunStatus({
  isAgentRunning,
  currentAgentStep,
  onStop,
  waitingForAnswer = false,
}) {
  if (!isAgentRunning) return null

  const step = Number(currentAgentStep)
  const hasStep = Number.isFinite(step) && step > 0
  const title = waitingForAnswer ? 'Agent is waiting for your answer' : 'Agent is running'
  const subtitle = waitingForAnswer
    ? "Answer the agent's question to continue this run."
    : 'Using tools as needed; it will keep going until the task is done.'

  return (
    <div
      role="group"
      aria-label="Agent run status"
      data-testid="agent-run-status"
      className="mb-2 px-3 py-2 rounded-lg border border-blue-500/60 bg-blue-950/60 text-blue-100 flex items-center gap-3"
    >
      <span className="relative flex h-2.5 w-2.5 flex-shrink-0" aria-hidden="true">
        <span className="animate-ping motion-reduce:animate-none absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-blue-400" />
      </span>
      <Sparkles className="w-4 h-4 flex-shrink-0 text-blue-300" aria-hidden="true" />
      {/* Only the state text is a live region, so a step change announces
          "Agent is running, step 3" rather than the whole row and its Stop. */}
      <div className="min-w-0 flex-1" role="status" aria-live="polite" aria-atomic="true">
        <div className="text-sm font-semibold text-blue-50">
          {title}
          {hasStep && !waitingForAnswer && (
            <span className="font-normal text-blue-200"> · step {step}</span>
          )}
        </div>
        <div className="text-xs text-blue-200">{subtitle}</div>
      </div>
      {typeof onStop === 'function' && (
        <button
          type="button"
          onClick={onStop}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-red-700 hover:bg-red-600 text-white text-xs font-medium transition-colors flex-shrink-0"
          title="Stop agent run"
          aria-label="Stop agent run"
        >
          <Square className="w-3.5 h-3.5" aria-hidden="true" />
          Stop
        </button>
      )}
    </div>
  )
}
