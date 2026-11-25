/**
 * Turns an MCP elicitation `requestedSchema` into form fields, and a filled
 * form back into the answer.
 *
 * The schema is the restricted, flat subset of JSON Schema the MCP
 * specification allows for elicitation: an object whose properties are
 * strings (optionally with a format or length limits), numbers, booleans,
 * single-choice enums (plain `enum`, or `oneOf`/`anyOf` of `const` + `title`,
 * or the older `enumNames`), and multi-choice arrays of those.
 */

function titledOptions(list) {
  if (!Array.isArray(list)) return null
  const options = list
    .filter(item => item && typeof item === 'object' && 'const' in item)
    .map(item => ({ value: item.const, label: String(item.title ?? item.const) }))
  return options.length > 0 ? options : null
}

function choiceOptions(prop) {
  if (!prop || typeof prop !== 'object') return null
  const titled = titledOptions(prop.oneOf) || titledOptions(prop.anyOf)
  if (titled) return titled
  if (Array.isArray(prop.enum)) {
    const names = Array.isArray(prop.enumNames) ? prop.enumNames : []
    return prop.enum.map((value, i) => ({ value, label: String(names[i] ?? value) }))
  }
  return null
}

/** Field descriptors for a requested schema; one `none` field for an empty schema. */
export function parseResponseSchema(schema) {
  if (!schema || typeof schema !== 'object') {
    return []
  }

  // An empty object schema asks for confirmation only.
  if (Object.keys(schema).length === 0 || (schema.properties && Object.keys(schema.properties).length === 0)) {
    return [{ name: 'none', type: 'none', required: false }]
  }

  const properties = schema.properties || {}
  const required = schema.required || []

  return Object.entries(properties).map(([name, prop]) => {
    const field = {
      name,
      type: prop.type || 'string',
      title: prop.title || '',
      description: prop.description || '',
      required: required.includes(name),
      default: prop.default,
      format: prop.format,
      minimum: prop.minimum,
      maximum: prop.maximum,
      minLength: prop.minLength,
      maxLength: prop.maxLength,
    }

    if (prop.type === 'array') {
      field.type = 'multiselect'
      field.options = choiceOptions(prop.items) || []
      field.minItems = prop.minItems
      field.maxItems = prop.maxItems
      return field
    }

    const options = choiceOptions(prop)
    if (options) {
      field.type = 'enum'
      field.options = options
      // Kept for callers that only need the raw values.
      field.enum = options.map(option => option.value)
    }
    return field
  })
}

/** The starting value of every field. */
export function initialFormData(fields) {
  const data = {}
  fields.forEach(field => {
    if (field.type === 'none') return
    if (field.default !== undefined) {
      data[field.name] = field.default
    } else if (field.type === 'boolean') {
      data[field.name] = false
    } else if (field.type === 'multiselect') {
      data[field.name] = []
    } else {
      data[field.name] = ''
    }
  })
  return data
}

function isBlank(value) {
  return value === '' || value === null || value === undefined
}

/** Whether one field holds an answer the server's schema will accept. */
export function isFieldValid(field, value) {
  if (field.type === 'none' || field.type === 'boolean') return true
  if (field.type === 'multiselect') {
    const count = Array.isArray(value) ? value.length : 0
    if (field.required && count === 0 && !(field.minItems === 0)) return false
    if (typeof field.minItems === 'number' && count < field.minItems && (field.required || count > 0)) return false
    if (typeof field.maxItems === 'number' && count > field.maxItems) return false
    return true
  }
  if (isBlank(value)) return !field.required
  if (field.type === 'number' || field.type === 'integer') {
    if (typeof value !== 'number' || Number.isNaN(value)) return false
    if (field.type === 'integer' && !Number.isInteger(value)) return false
    if (typeof field.minimum === 'number' && value < field.minimum) return false
    if (typeof field.maximum === 'number' && value > field.maximum) return false
    return true
  }
  if (field.type === 'string') {
    const text = String(value)
    if (typeof field.minLength === 'number' && text.length < field.minLength) return false
    if (typeof field.maxLength === 'number' && text.length > field.maxLength) return false
  }
  return true
}

/** Whether the whole form can be accepted. */
export function isFormValid(fields, formData) {
  return fields.every(field => isFieldValid(field, formData[field.name]))
}

/**
 * The `data` of an accepted elicitation response.
 *
 * Confirmation-only requests answer null, a single `value` field answers the
 * bare value, anything else answers an object. Optional fields left blank are
 * omitted: the schema has no null type, so a blank optional number or choice
 * would fail the server's validation.
 */
export function buildResponseData(fields, formData) {
  const inputs = fields.filter(field => field.type !== 'none')
  if (inputs.length === 0) return null
  if (inputs.length === 1 && inputs[0].name === 'value') {
    return wireValue(inputs[0], formData.value)
  }
  const data = {}
  inputs.forEach(field => {
    const value = formData[field.name]
    if (!field.required && isBlank(value)) return
    data[field.name] = wireValue(field, value)
  })
  return data
}

// A datetime-local input yields local wall-clock time with no offset, which
// is not a valid `date-time`; send the instant it names instead.
function wireValue(field, value) {
  if (field.format === 'date-time' && typeof value === 'string' && value) {
    const instant = new Date(value)
    return Number.isNaN(instant.getTime()) ? value : instant.toISOString()
  }
  return value
}

/** The label shown for a field: its schema title, else a readable form of its name. */
export function fieldLabel(field) {
  if (field.title) return field.title
  if (field.name === 'value') return 'Value'
  return field.name
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/** `url` when it is an absolute http(s) URL, otherwise null. */
export function safeElicitationUrl(url) {
  if (typeof url !== 'string' || !url.trim()) return null
  try {
    const parsed = new URL(url.trim())
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

const INPUT_TYPE_BY_FORMAT = {
  email: 'email',
  uri: 'url',
  date: 'date',
  'date-time': 'datetime-local',
}

/** The HTML input type for a string field's `format`. */
export function inputTypeForFormat(format) {
  return INPUT_TYPE_BY_FORMAT[format] || 'text'
}
