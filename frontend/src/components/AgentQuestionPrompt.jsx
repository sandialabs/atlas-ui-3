import { useState } from 'react'

/**
 * "Agent needs your input" card shown in the transcript while an agent run
 * waits on the user. Kept as its own component so the browser contrast check
 * (frontend/scripts/check-retro-browser.py) can mount the shipped markup.
 */
export default function AgentQuestionPrompt({ question, onAnswer }) {
  const [answer, setAnswer] = useState('')
  const submit = () => {
    const trimmed = answer.trim()
    if (!trimmed) return
    onAnswer(trimmed)
    setAnswer('')
  }
  return (
    <div className="flex items-start gap-0 sm:gap-3 w-full">
      <div className="w-8 h-8 rounded-full bg-purple-600 items-center justify-center text-white text-sm font-medium flex-shrink-0 hidden sm:flex">
        A
      </div>
      <div className="flex-1 min-w-0 bg-gray-800 rounded-lg p-3 sm:p-4 border border-purple-700">
        <div className="text-sm font-medium atlas-fg-agent mb-2">Agent needs your input</div>
        <div className="text-gray-200 mb-3">{question}</div>
        <div className="flex gap-2">
          <input
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Type your answer..."
            aria-label="Answer for the agent"
            className="flex-1 px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-gray-200"
          />
          <button
            type="button"
            onClick={submit}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  )
}
