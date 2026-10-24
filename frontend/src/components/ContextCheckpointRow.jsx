import { useState } from 'react'
import { Archive, ChevronDown, ChevronRight } from 'lucide-react'
import { FOCUS_RING } from '../utils/focusRing'

// A compacted-context event in the transcript (issue #877). The row records
// where compaction happened and the before/after estimate; the checkpoint the
// model now sees in place of the earlier messages can be expanded below it.
// The earlier messages themselves are never removed from the transcript.
const ContextCheckpointRow = ({ message }) => {
  const [open, setOpen] = useState(false)
  const record = message.context_checkpoint || {}
  const summary = typeof record.summary === 'string' ? record.summary : ''
  const runScope = record.scope === 'run'

  return (
    <div className="my-1 text-xs text-gray-400" data-testid="context-checkpoint-row">
      <div className="flex items-center gap-2">
        <div className="h-px flex-1 bg-gray-700" aria-hidden="true" />
        <Archive className="w-3.5 h-3.5 flex-shrink-0 text-sky-400" aria-hidden="true" />
        <span className="min-w-0 break-words">{message.content}</span>
        {summary && (
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className={`flex items-center gap-0.5 rounded px-1 text-sky-300 hover:text-sky-200 ${FOCUS_RING}`}
          >
            {open ? <ChevronDown className="w-3 h-3" aria-hidden="true" /> : <ChevronRight className="w-3 h-3" aria-hidden="true" />}
            <span>{open ? 'Hide checkpoint' : 'Show checkpoint'}</span>
          </button>
        )}
        <div className="h-px flex-1 bg-gray-700" aria-hidden="true" />
      </div>
      {open && summary && (
        <div className="mt-2 rounded border border-gray-700 bg-gray-800/60 p-3 text-gray-300">
          <p className="mb-2 text-gray-400">
            {runScope
              ? 'The agent continued this run from this checkpoint. Earlier messages are still in the transcript above.'
              : 'Later requests carry this checkpoint instead of the earlier messages, which stay in the transcript above.'}
          </p>
          <pre className="whitespace-pre-wrap break-words font-sans">{summary}</pre>
        </div>
      )}
    </div>
  )
}

export default ContextCheckpointRow
