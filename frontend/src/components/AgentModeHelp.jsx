import { useId, useRef, useState } from 'react'
import { CircleHelp, Wrench } from 'lucide-react'
import { useEscapeKey } from '../hooks/useEscapeKey'
import { openSettingsPanel } from '../utils/settingsPanelEvents'

/**
 * Progressive disclosure for Agent Mode.
 *
 * Agent Mode is on by default, but its icon-only header toggle never said what
 * it does. A permanent paragraph in the toolbar would be noise for the people
 * who already use it, so the explanation lives behind a question-mark button
 * next to the toggle. `quiet` (the "Reduce helper text" preference) removes the
 * button entirely; the toggle, its state, and its shortcut stay.
 */
export function AgentModeExplanation({ className = '' }) {
  return (
    <div className={`space-y-2 text-xs text-gray-300 ${className}`}>
      <p>
        <span className="font-semibold text-gray-100">Agent Mode</span> lets the assistant work
        through a request in multiple steps: it calls the tools you have selected, inspects the
        results, and keeps going until it can answer or decides the task is done.
      </p>
      <p>
        Without selected tools it behaves like normal chat. It also needs a model that supports
        tool calling.
      </p>
    </div>
  )
}

export function AgentModeHelpButton({ quiet = false }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const buttonRef = useRef(null)

  useEscapeKey(open, () => {
    setOpen(false)
    buttonRef.current?.focus()
  })

  if (quiet) return null

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen(previous => !previous)}
        aria-label="What Agent Mode does"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title="What Agent Mode does"
        className="p-1 rounded-md text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
      >
        <CircleHelp className="w-4 h-4" aria-hidden="true" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            id={panelId}
            role="dialog"
            aria-label="What Agent Mode does"
            className="absolute right-0 top-full mt-2 w-80 max-w-[calc(100vw-2rem)] bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 p-3"
          >
            <p className="text-sm font-semibold text-gray-100">Agent Mode</p>
            <AgentModeExplanation className="mt-1" />
            <div className="mt-3 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  openSettingsPanel({ tab: 'tools' })
                }}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium transition-colors"
              >
                <Wrench className="w-3.5 h-3.5" aria-hidden="true" />
                Choose tools
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="px-2.5 py-1.5 rounded-md text-gray-300 hover:text-gray-100 hover:bg-gray-700 text-xs transition-colors"
              >
                Close
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/** One-line summary for the compact header menu, where there is room for text. */
export function AgentModeMenuHint() {
  return (
    <span className="block text-xs font-normal opacity-75">
      Multi-step tool use. Behaves like normal chat when no tools are selected.
    </span>
  )
}

export default AgentModeHelpButton
