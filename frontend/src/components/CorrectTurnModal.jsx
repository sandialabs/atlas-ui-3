import { useState, useRef, useEffect } from 'react'
import { X, RotateCcw } from 'lucide-react'

/**
 * CorrectTurnModal
 *
 * Small modal for the fine-tune capture "correct this turn" affordance (issue
 * #622). Lets the user pick the tool the assistant should have used and add an
 * optional note, then re-runs the turn forcing that tool. Styled to match the
 * app's existing form modals (TokenInputModal).
 *
 * Props:
 *   isOpen        - whether the modal is shown.
 *   toolOptions   - Array<{ value, label }> of selectable tools.
 *   onClose       - close handler.
 *   onSubmit      - (chosenTool, note) => void; dispatches the correction.
 */
const CorrectTurnModal = ({ isOpen, toolOptions = [], onClose, onSubmit }) => {
  const [chosenTool, setChosenTool] = useState('')
  const [note, setNote] = useState('')
  const selectRef = useRef(null)

  // Reset fields each time the modal opens.
  useEffect(() => {
    if (isOpen) {
      setChosenTool('')
      setNote('')
      // Focus the select for keyboard users.
      requestAnimationFrame(() => selectRef.current?.focus())
    }
  }, [isOpen])

  if (!isOpen) return null

  const handleSubmit = () => {
    if (!chosenTool) return
    onSubmit(chosenTool, note.trim())
  }

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-[100]"
      onClick={onClose}
    >
      <div
        className="bg-gray-800 rounded-lg shadow-xl max-w-lg w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-100 flex items-center gap-2">
            <RotateCcw className="w-5 h-5" />
            Which tool should the assistant have used?
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-700 transition-colors"
            aria-label="Close"
            type="button"
          >
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Correct tool
            </label>
            <select
              ref={selectRef}
              value={chosenTool}
              onChange={(e) => setChosenTool(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Select a tool...</option>
              {toolOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            {toolOptions.length === 0 && (
              <p className="text-xs text-gray-500 mt-1">
                No tools are available to choose from.
              </p>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Note (optional)
            </label>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="Why was the original response wrong?"
              className="w-full px-3 py-2 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
            />
          </div>

          <p className="text-xs text-gray-400">
            This re-runs the turn forcing the selected tool. The original
            (incorrect) response is recorded alongside the correction to help
            fine-tune a local model.
          </p>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
            type="button"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!chosenTool}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            type="button"
          >
            <RotateCcw className="w-4 h-4" />
            Re-run with this tool
          </button>
        </div>
      </div>
    </div>
  )
}

export default CorrectTurnModal
