import { useState, useEffect, useRef } from 'react'
import { X, Check, Ban, ExternalLink } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useFocusTrap } from '../utils/focusTrap'
import {
  buildResponseData,
  fieldLabel,
  initialFormData,
  inputTypeForFormat,
  isFormValid,
  parseResponseSchema,
  safeElicitationUrl,
} from '../utils/elicitationSchema'

/**
 * ElicitationDialog Component
 *
 * Displays a modal dialog to collect user input when an MCP tool asks for it
 * (an MCP elicitation request). Supports:
 * - String, number, boolean, single-choice and multi-choice fields
 * - Structured multi-field forms
 * - URL requests, where the server asks the user to open an external page
 * - Accept/Decline/Cancel actions
 */
const ElicitationForm = ({ elicitation }) => {
  const { sendMessage, settleElicitation } = useChat()
  const [formData, setFormData] = useState({})
  const [sendFailed, setSendFailed] = useState(false)
  const dialogRef = useRef(null)
  // The prompt claims to be modal (aria-modal), so it must own focus: it
  // pulls focus in on mount, traps Tab while up, and stands down as the
  // topmost dialog (an open drawer beneath it must not take the keys back).
  // The entry target is the first form field -- or Accept when there are no
  // fields -- never the Cancel button: the prompt often mounts while the
  // user is typing, and focus on Cancel would make the next Space or Enter
  // cancel the tool's request.
  useFocusTrap({
    containerRef: dialogRef,
    active: true,
    initialFocusSelector: 'input, select, textarea, #elicitation-accept'
  })

  const { elicitation_id, tool_name, message, response_schema, mode, url } = elicitation
  const isUrlMode = mode === 'url'
  // The server already refuses non-web URLs; checking again here means a
  // javascript: or data: link can never be rendered as something to click.
  const safeUrl = isUrlMode ? safeElicitationUrl(url) : null

  // Parse the response schema to determine field types
  const fields = isUrlMode ? [] : parseResponseSchema(response_schema)
  const inputFields = fields.filter(field => field.type !== 'none')

  // Initialize form data with default values
  useEffect(() => {
    setFormData(initialFormData(fields))
    setSendFailed(false)
  }, [elicitation_id])

  const isValid = isUrlMode ? Boolean(safeUrl) : isFormValid(fields, formData)

  const handleFieldChange = (fieldName, value) => {
    setFormData(prev => ({
      ...prev,
      [fieldName]: value
    }))
  }

  // Close the dialog only when the answer reached the server. A dropped
  // answer would leave the tool waiting out its timeout while the prompt is
  // gone, so on failure the prompt stays up and says so. Cancel is the way out
  // of a long outage: it always dismisses, and the tool times out on its own.
  const respond = (action, data, { dismissAnyway = false } = {}) => {
    const sent = sendMessage({
      type: 'elicitation_response',
      elicitation_id,
      action,
      data
    })
    if (sent !== true && !dismissAnyway) {
      setSendFailed(true)
      return false
    }
    setSendFailed(false)
    settleElicitation(elicitation_id)
    return true
  }

  const handleAccept = () => {
    if (isUrlMode) {
      // Accepting a URL request is consent to open the page; it carries no
      // data. The page opens only once that consent has reached the server.
      if (safeUrl && respond('accept', null)) {
        window.open(safeUrl, '_blank', 'noopener,noreferrer')
      }
      return
    }
    respond('accept', buildResponseData(fields, formData))
  }

  const handleDecline = () => {
    respond('decline', null)
  }

  const handleCancel = () => {
    respond('cancel', null, { dismissAnyway: true })
  }

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-labelledby="elicitation-dialog-title"
      className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4"
    >
      <div className="bg-gray-800 rounded-lg shadow-2xl border border-gray-700 max-w-2xl w-full max-h-[80vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-700">
          <div>
            <h2 id="elicitation-dialog-title" className="text-xl font-semibold text-gray-50">User Input Required</h2>
            <p className="text-sm text-gray-400 mt-1">Tool: {tool_name}</p>
          </div>
          <button
            onClick={handleCancel}
            className="text-gray-400 hover:text-white transition-colors"
            title="Cancel"
          >
            <X size={24} />
          </button>
        </div>

        {/* Message */}
        <div className="p-6 border-b border-gray-700">
          <p className="text-gray-200 whitespace-pre-wrap">{message}</p>
        </div>

        {isUrlMode ? (
          <div className="p-6 space-y-3">
            <p className="text-sm text-gray-300">
              This tool is asking you to open an external page. Check the address before you continue.
            </p>
            {safeUrl ? (
              <p className="font-mono text-sm text-gray-100 break-all bg-gray-900 border border-gray-700 rounded-md p-3">
                {safeUrl}
              </p>
            ) : (
              <p role="alert" className="text-sm text-red-400">
                The link this tool sent is not a web address, so it cannot be opened.
              </p>
            )}
          </div>
        ) : (
          <div className="p-6 space-y-4">
            {inputFields.length === 0 ? (
              <p className="text-sm text-gray-400 italic">No additional information required. Please confirm or decline.</p>
            ) : (
              inputFields.map(field => (
                <FormField
                  key={field.name}
                  field={field}
                  value={formData[field.name]}
                  onChange={(value) => handleFieldChange(field.name, value)}
                />
              ))
            )}
          </div>
        )}

        {sendFailed && (
          <p role="alert" className="px-6 pb-4 text-sm text-red-400">
            Your answer was not sent because the connection is down. Try again once it is back.
          </p>
        )}

        {/* Actions */}
        <div className="flex flex-row-reverse items-center justify-start gap-3 p-6 border-t border-gray-700 bg-gray-750">
          <button
            id="elicitation-accept"
            onClick={handleAccept}
            disabled={!isValid}
            className={`px-4 py-2 rounded-md transition-colors flex items-center gap-2 ${
              isValid
                ? 'bg-green-600 hover:bg-green-500 text-white'
                : 'bg-gray-600 text-gray-400 cursor-not-allowed'
            }`}
          >
            {isUrlMode ? <ExternalLink size={16} /> : <Check size={16} />}
            {isUrlMode ? 'Open link' : 'Accept'}
          </button>
          <button
            onClick={handleDecline}
            className="px-4 py-2 rounded-md bg-yellow-600 hover:bg-yellow-500 text-white transition-colors flex items-center gap-2"
          >
            <Ban size={16} />
            Decline
          </button>
          <button
            onClick={handleCancel}
            className="px-4 py-2 rounded-md bg-gray-700 hover:bg-gray-600 text-gray-50 transition-colors flex items-center gap-2"
          >
            <X size={16} />
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

const INPUT_CLASS = 'w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500'

/**
 * FormField Component
 * Renders appropriate input based on field type
 */
const FormField = ({ field, value, onChange }) => {
  const { name, type, description, required, options } = field
  const label = fieldLabel(field)

  const renderInput = () => {
    switch (type) {
      case 'enum': {
        // Option values keep their JSON type (a choice may be a number), so
        // the select works on indexes rather than on stringified values.
        const selected = options.findIndex(option => option.value === value)
        return (
          <select
            value={selected === -1 ? '' : String(selected)}
            onChange={(e) => onChange(e.target.value === '' ? '' : options[Number(e.target.value)].value)}
            className={INPUT_CLASS}
            required={required}
            aria-label={label}
          >
            <option value="">Select an option...</option>
            {options.map((option, index) => (
              <option key={String(option.value)} value={String(index)}>{option.label}</option>
            ))}
          </select>
        )
      }

      case 'multiselect': {
        const chosen = Array.isArray(value) ? value : []
        const toggle = (optionValue, checked) => {
          // Rebuilt in option order so the answer does not depend on click order.
          onChange(options
            .map(option => option.value)
            .filter(v => (v === optionValue ? checked : chosen.includes(v))))
        }
        return (
          <div role="group" aria-label={label} className="space-y-2">
            {options.map(option => (
              <label key={String(option.value)} className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={chosen.includes(option.value)}
                  onChange={(e) => toggle(option.value, e.target.checked)}
                  className="w-5 h-5 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-2 focus:ring-blue-500"
                />
                <span className="text-gray-300">{option.label}</span>
              </label>
            ))}
          </div>
        )
      }

      case 'number':
      case 'integer':
        return (
          <input
            type="number"
            value={value === '' ? '' : (value ?? '')}
            onChange={(e) => onChange(e.target.value === '' ? '' : parseFloat(e.target.value))}
            className={INPUT_CLASS}
            placeholder={description || `Enter ${label}`}
            required={required}
            min={field.minimum}
            max={field.maximum}
            step={type === 'integer' ? '1' : 'any'}
            aria-label={label}
          />
        )

      case 'boolean':
        return (
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={value || false}
              onChange={(e) => onChange(e.target.checked)}
              className="w-5 h-5 rounded bg-gray-700 border-gray-600 text-blue-600 focus:ring-2 focus:ring-blue-500"
            />
            <span className="text-gray-300">
              {description || field.title || `Enable ${name}`}
            </span>
          </label>
        )

      default:
        return (
          <input
            type={inputTypeForFormat(field.format)}
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            className={INPUT_CLASS}
            placeholder={description || `Enter ${label}`}
            required={required}
            minLength={field.minLength}
            maxLength={field.maxLength}
            aria-label={label}
          />
        )
    }
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-300 mb-2">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
      {description && type !== 'boolean' && (
        <p className="text-xs text-gray-400 mb-2">{description}</p>
      )}
      {renderInput()}
    </div>
  )
}

/**
 * One dialog per question. Keyed on the request id so that when a queued
 * question takes the place of the one just answered it mounts fresh: its
 * fields start empty and focus moves into it, instead of staying on the
 * previous question's button where the next Enter would answer this one unseen.
 */
const ElicitationDialog = ({ elicitation }) => (
  <ElicitationForm key={elicitation.elicitation_id} elicitation={elicitation} />
)

export default ElicitationDialog
