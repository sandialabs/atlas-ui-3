import { useState, useEffect } from 'react'
import { X, Check, Ban } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

/**
 * ElicitationDialog Component
 * 
 * Displays a modal dialog to collect user input when an MCP tool requests
 * elicitation via ctx.elicit(). Supports:
 * - String, number, boolean, enum fields
 * - Structured multi-field forms
 * - Accept/Decline/Cancel actions
 */
const ElicitationDialog = ({ elicitation }) => {
  const { sendMessage, setPendingElicitation } = useChat()
  const [formData, setFormData] = useState({})
  const [isValid, setIsValid] = useState(false)

  const { elicitation_id, tool_call_id, tool_name, message, response_schema } = elicitation

  // Parse the response schema to determine field types
  const fields = parseResponseSchema(response_schema)

  // Initialize form data with default values
  useEffect(() => {
    const initialData = {}
    fields.forEach(field => {
      if (field.default !== undefined) {
        initialData[field.name] = field.default
      } else if (field.type === 'boolean') {
        initialData[field.name] = false
      } else {
        initialData[field.name] = ''
      }
    })
    setFormData(initialData)
  }, [elicitation_id])

  // Validate form data
  useEffect(() => {
    const requiredFields = fields.filter(f => f.required && f.type !== 'none')
    const allRequiredFilled = requiredFields.every(field => {
      const value = formData[field.name]
      if (field.type === 'number') {
        return value !== '' && value !== null && value !== undefined && !isNaN(value)
      }
      return value !== '' && value !== null && value !== undefined
    })
    setIsValid(allRequiredFilled)
  }, [formData, fields])

  const handleFieldChange = (fieldName, value) => {
    setFormData(prev => ({
      ...prev,
      [fieldName]: value
    }))
  }

  const handleAccept = () => {
    // Prepare response data based on schema type
    let responseData
    if (fields.length === 0) {
      // No response expected (approval-only)
      responseData = null
    } else if (fields.length === 1 && fields[0].name === 'value') {
      // Scalar type - unwrap the value field
      responseData = formData.value
    } else {
      // Structured type - send all fields
      responseData = { ...formData }
    }

    sendMessage({
      type: 'elicitation_response',
      elicitation_id,
      action: 'accept',
      data: responseData
    })

    // Close the dialog
    setPendingElicitation(null)
  }

  const handleDecline = () => {
    sendMessage({
      type: 'elicitation_response',
      elicitation_id,
      action: 'decline',
      data: null
    })

    // Close the dialog
    setPendingElicitation(null)
  }

  const handleCancel = () => {
    sendMessage({
      type: 'elicitation_response',
      elicitation_id,
      action: 'cancel',
      data: null
    })

    // Close the dialog
    setPendingElicitation(null)
  }

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 rounded-lg shadow-2xl border border-gray-700 max-w-2xl w-full max-h-[80vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-700">
          <div>
            <h2 className="text-xl font-semibold text-white">User Input Required</h2>
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

        {/* Form Fields */}
        <div className="p-6 space-y-4">
          {fields.length === 0 ? (
            <p className="text-sm text-gray-400 italic">No additional information required. Please confirm or decline.</p>
          ) : (
            fields.map(field => (
              <FormField
                key={field.name}
                field={field}
                value={formData[field.name]}
                onChange={(value) => handleFieldChange(field.name, value)}
              />
            ))
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 p-6 border-t border-gray-700 bg-gray-750">
          <button
            onClick={handleCancel}
            className="px-4 py-2 rounded-md bg-gray-700 hover:bg-gray-600 text-white transition-colors flex items-center gap-2"
          >
            <X size={16} />
            Cancel
          </button>
          <button
            onClick={handleDecline}
            className="px-4 py-2 rounded-md bg-yellow-600 hover:bg-yellow-500 text-white transition-colors flex items-center gap-2"
          >
            <Ban size={16} />
            Decline
          </button>
          <button
            onClick={handleAccept}
            disabled={!isValid && fields.length > 0}
            className={`px-4 py-2 rounded-md transition-colors flex items-center gap-2 ${
              isValid || fields.length === 0
                ? 'bg-green-600 hover:bg-green-500 text-white'
                : 'bg-gray-600 text-gray-400 cursor-not-allowed'
            }`}
          >
            <Check size={16} />
            Accept
          </button>
        </div>
      </div>
    </div>
  )
}

/**
 * FormField Component
 * Renders appropriate input based on field type
 */
const FormField = ({ field, value, onChange }) => {
  const { name, type, description, required, enum: enumValues } = field

  const renderInput = () => {
    switch (type) {
      case 'string':
        if (enumValues && enumValues.length > 0) {
          // String enum - render as dropdown
          return (
            <select
              value={value || ''}
              onChange={(e) => onChange(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              required={required}
            >
              <option value="">Select an option...</option>
              {enumValues.map(opt => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          )
        }
        // Regular string input
        return (
          <input
            type="text"
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={description || `Enter ${name}`}
            required={required}
          />
        )

      case 'number':
      case 'integer':
        return (
          <input
            type="number"
            value={value === '' ? '' : value}
            onChange={(e) => onChange(e.target.value === '' ? '' : parseFloat(e.target.value))}
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={description || `Enter ${name}`}
            required={required}
            step={type === 'integer' ? '1' : 'any'}
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
              {description || `Enable ${name}`}
            </span>
          </label>
        )

      case 'enum':
        return (
          <select
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            required={required}
          >
            <option value="">Select an option...</option>
            {enumValues.map(opt => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        )

      default:
        return (
          <input
            type="text"
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-md text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder={description || `Enter ${name}`}
            required={required}
          />
        )
    }
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-300 mb-2">
        {formatFieldName(name)}
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
 * Parse JSON schema to extract field information
 */
function parseResponseSchema(schema) {
  if (!schema || typeof schema !== 'object') {
    return []
  }

  // Handle empty object schema (approval-only)
  if (Object.keys(schema).length === 0 || (schema.properties && Object.keys(schema.properties).length === 0)) {
    return [{
      name: 'none',
      type: 'none',
      required: false
    }]
  }

  const properties = schema.properties || {}
  const required = schema.required || []
  
  return Object.entries(properties).map(([name, prop]) => {
    const field = {
      name,
      type: prop.type || 'string',
      description: prop.description || '',
      required: required.includes(name),
      default: prop.default
    }

    // Handle enum values
    if (prop.enum && Array.isArray(prop.enum)) {
      field.enum = prop.enum
      field.type = 'enum'
    }

    return field
  })
}

/**
 * Format field name for display (convert snake_case to Title Case)
 */
function formatFieldName(name) {
  if (name === 'value') return 'Value'
  return name
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export default ElicitationDialog
