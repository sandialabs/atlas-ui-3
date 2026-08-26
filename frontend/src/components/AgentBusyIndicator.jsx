/**
 * Progress indicator shown while the agent loop is between steps.
 *
 * The existing "Thinking..." indicator is gated on `isThinking`, which the
 * websocket handlers clear as soon as the first token of a turn streams and
 * never set again for later turns. That leaves a silent gap: after a tool
 * completes and the loop calls the LLM for the next turn, nothing in the chat
 * area moves and the run looks frozen. This fills that gap without touching the
 * `isThinking` state machine -- it renders only when the agent is running and
 * neither the thinking indicator nor a token stream is already visible.
 */

export default function AgentBusyIndicator({
  isAgentRunning,
  isThinking,
  isStreaming,
  currentAgentStep,
  appName = 'Agent',
}) {
  if (!isAgentRunning || isThinking || isStreaming) return null

  const step = Number(currentAgentStep)
  const label = Number.isFinite(step) && step > 0
    ? `Agent working on step ${step}...`
    : 'Agent thinking...'

  return (
    <div className="flex items-start gap-0 sm:gap-3 w-full" data-testid="agent-busy-indicator">
      <div className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center text-white text-sm font-medium flex-shrink-0 hidden sm:flex">
        A
      </div>
      {/* flex-1 min-w-0, not w-full: as a flex sibling of the avatar, a
          w-full bubble claims the whole row on top of the avatar and gap. */}
      <div className="flex-1 min-w-0 bg-gray-800 rounded-lg p-3 sm:p-4">
        <div className="text-sm font-medium text-gray-300 mb-2">{appName}</div>
        <div className="flex items-center gap-2 text-gray-400">
          <svg className="w-4 h-4 spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span>{label}</span>
        </div>
      </div>
    </div>
  )
}
