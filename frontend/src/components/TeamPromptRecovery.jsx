import { useEffect, useRef, useState } from 'react'
import { useChat } from '../contexts/ChatContext'

/**
 * "Clear team prompt" for a conversation whose team-prompt pin blocks it
 * (issue #1169): the pinned prompt is no longer available to the user, or the
 * stored pin cannot be read. The server decides who may clear (the
 * conversation's owner) and refuses everyone else; this is only the browser's
 * way to ask, with a confirmation first, because clearing changes which prompt
 * the conversation runs with from now on.
 *
 * It always acts on `conversationId` -- the conversation the refusal named --
 * whatever is on screen when the button is pressed.
 */
export default function TeamPromptRecovery({ conversationId, problem = null, revision = null }) {
  const { clearConversationTeamPrompt } = useChat()
  // idle -> confirming -> clearing -> done | failed
  const [state, setState] = useState('idle')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const confirmRef = useRef(null)
  const startRef = useRef(null)

  useEffect(() => {
    if (state === 'confirming') confirmRef.current?.focus()
  }, [state])

  if (typeof clearConversationTeamPrompt !== 'function' || !conversationId) return null

  const clear = async () => {
    setState('clearing')
    setError(null)
    try {
      setResult(await clearConversationTeamPrompt(conversationId, revision))
      setState('done')
    } catch (e) {
      setError(e?.message || 'Could not clear the team prompt.')
      setState('failed')
    }
  }

  const cancel = () => {
    setState('idle')
    // Return focus to the control that opened the confirmation.
    setTimeout(() => startRef.current?.focus(), 0)
  }

  if (state === 'done' && result?.classificationUnknown) {
    return (
      <div role="status" className="mt-2 text-sm text-yellow-300">
        The damaged team-prompt settings were reset, but this conversation&apos;s other stored
        settings could not be read either, so it stays closed to new messages. Start a new
        conversation to keep working.
      </div>
    )
  }

  if (state === 'done') {
    return (
      <div role="status" className="mt-2 text-sm text-green-300">
        {result?.recovered
          ? 'The damaged team-prompt settings were reset. '
          : result?.cleared
            ? "This conversation's team prompt was cleared. "
            : 'This conversation has no team prompt any more. '}
        Send your message again to continue with the default prompt.
      </div>
    )
  }

  const explanation = problem === 'damaged'
    ? "This conversation's stored team-prompt settings are damaged."
    : 'The team prompt this conversation is pinned to is no longer available to you.'

  return (
    <div className="mt-2 space-y-2 text-sm">
      {state === 'confirming' ? (
        <div
          role="group"
          aria-label="Confirm clearing the team prompt"
          className="p-3 rounded border border-yellow-700 bg-yellow-900/20 text-gray-200 space-y-2"
        >
          <p>
            {explanation} Clear it? The conversation keeps its history, and your next message
            uses the default prompt. You can choose another prompt afterwards.
          </p>
          <div className="flex gap-2">
            <button
              ref={confirmRef}
              type="button"
              onClick={clear}
              className="px-3 py-1 rounded bg-yellow-700 hover:bg-yellow-600 text-white font-medium"
            >
              Clear team prompt
            </button>
            <button
              type="button"
              onClick={cancel}
              className="px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          ref={startRef}
          type="button"
          onClick={() => setState('confirming')}
          disabled={state === 'clearing'}
          aria-busy={state === 'clearing'}
          className="px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 font-medium disabled:opacity-60"
        >
          {state === 'clearing' ? 'Clearing team prompt...' : state === 'failed' ? 'Try clearing again' : 'Clear team prompt'}
        </button>
      )}
      {state === 'failed' && error && (
        <div role="alert" className="text-red-300">{error}</div>
      )}
    </div>
  )
}
