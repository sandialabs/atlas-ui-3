/**
 * Tests for the elicitation schema and queue utilities: turning an MCP
 * `requestedSchema` into form fields, building the answer, and holding
 * several pending requests.
 */

import { describe, it, expect } from 'vitest'
import {
  buildResponseData,
  fieldLabel,
  initialFormData,
  isFieldValid,
  isFormValid,
  parseResponseSchema,
  safeElicitationUrl,
} from '../utils/elicitationSchema'
import { enqueueElicitation, settleElicitation } from '../utils/elicitationQueue'

const schemaOf = (properties, required = []) => ({ type: 'object', properties, required })

describe('parseResponseSchema', () => {
  it('treats an empty schema as confirmation only', () => {
    expect(parseResponseSchema({ type: 'object', properties: {} })).toEqual([
      { name: 'none', type: 'none', required: false },
    ])
    expect(parseResponseSchema(null)).toEqual([])
  })

  it('reads a plain enum as a single choice', () => {
    const [field] = parseResponseSchema(schemaOf({ value: { type: 'string', enum: ['red', 'blue'] } }, ['value']))
    expect(field.type).toBe('enum')
    expect(field.required).toBe(true)
    expect(field.options).toEqual([
      { value: 'red', label: 'red' },
      { value: 'blue', label: 'blue' },
    ])
  })

  it('reads titled choices from oneOf, anyOf and enumNames', () => {
    const fields = parseResponseSchema(schemaOf({
      size: { type: 'string', oneOf: [{ const: 's', title: 'Small' }, { const: 'l', title: 'Large' }] },
      tier: { type: 'string', anyOf: [{ const: 'a', title: 'Alpha' }] },
      old: { type: 'string', enum: ['x', 'y'], enumNames: ['Ex', 'Why'] },
    }))
    expect(fields.map(f => f.type)).toEqual(['enum', 'enum', 'enum'])
    expect(fields[0].options).toEqual([{ value: 's', label: 'Small' }, { value: 'l', label: 'Large' }])
    expect(fields[1].options).toEqual([{ value: 'a', label: 'Alpha' }])
    expect(fields[2].options).toEqual([{ value: 'x', label: 'Ex' }, { value: 'y', label: 'Why' }])
  })

  it('reads an array of choices as a multi-select', () => {
    const [plain, titled] = parseResponseSchema(schemaOf({
      toppings: { type: 'array', items: { type: 'string', enum: ['a', 'b'] }, minItems: 1, maxItems: 2 },
      extras: { type: 'array', items: { anyOf: [{ const: 'x', title: 'Extra X' }] } },
    }))
    expect(plain.type).toBe('multiselect')
    expect(plain.options.map(o => o.value)).toEqual(['a', 'b'])
    expect(plain.minItems).toBe(1)
    expect(plain.maxItems).toBe(2)
    expect(titled.options).toEqual([{ value: 'x', label: 'Extra X' }])
  })

  it('carries titles, formats and limits', () => {
    const [age, email] = parseResponseSchema(schemaOf({
      age: { type: 'integer', title: 'Your age', minimum: 0, maximum: 120 },
      email: { type: 'string', format: 'email', maxLength: 50 },
    }))
    expect(fieldLabel(age)).toBe('Your age')
    expect(age.minimum).toBe(0)
    expect(age.maximum).toBe(120)
    expect(email.format).toBe('email')
    expect(fieldLabel(email)).toBe('Email')
    expect(fieldLabel({ name: 'due_date' })).toBe('Due Date')
  })
})

describe('form validity', () => {
  const fields = parseResponseSchema(schemaOf({
    name: { type: 'string', minLength: 2 },
    count: { type: 'integer', minimum: 1, maximum: 5 },
    note: { type: 'string' },
    picks: { type: 'array', items: { type: 'string', enum: ['a', 'b', 'c'] }, maxItems: 2 },
  }, ['name', 'count', 'picks']))
  const [name, count, note, picks] = fields

  it('requires required fields and leaves optional ones free', () => {
    expect(isFieldValid(name, '')).toBe(false)
    expect(isFieldValid(name, 'A')).toBe(false)
    expect(isFieldValid(name, 'Al')).toBe(true)
    expect(isFieldValid(note, '')).toBe(true)
  })

  it('holds numbers to their type and range', () => {
    expect(isFieldValid(count, '')).toBe(false)
    expect(isFieldValid(count, 2.5)).toBe(false)
    expect(isFieldValid(count, 0)).toBe(false)
    expect(isFieldValid(count, 6)).toBe(false)
    expect(isFieldValid(count, 3)).toBe(true)
  })

  it('holds multi-selects to their size', () => {
    expect(isFieldValid(picks, [])).toBe(false)
    expect(isFieldValid(picks, ['a'])).toBe(true)
    expect(isFieldValid(picks, ['a', 'b', 'c'])).toBe(false)
  })

  it('accepts a form only when every field is valid', () => {
    const start = initialFormData(fields)
    expect(start).toEqual({ name: '', count: '', note: '', picks: [] })
    expect(isFormValid(fields, start)).toBe(false)
    expect(isFormValid(fields, { name: 'Al', count: 3, note: '', picks: ['a'] })).toBe(true)
  })
})

describe('buildResponseData', () => {
  it('answers null for a confirmation-only request', () => {
    const fields = parseResponseSchema({ type: 'object', properties: {} })
    expect(buildResponseData(fields, {})).toBeNull()
  })

  it('answers the bare value for a single value field', () => {
    const fields = parseResponseSchema(schemaOf({ value: { type: 'array', items: { enum: ['a', 'b'] } } }))
    expect(buildResponseData(fields, { value: ['b'] })).toEqual(['b'])
  })

  it('omits optional fields that were left blank', () => {
    const fields = parseResponseSchema(schemaOf({
      title: { type: 'string' },
      nickname: { type: 'string' },
      age: { type: 'integer' },
      subscribed: { type: 'boolean' },
    }, ['title']))
    const data = buildResponseData(fields, { title: 'Dr', nickname: '', age: '', subscribed: false })
    expect(data).toEqual({ title: 'Dr', subscribed: false })
  })

  it('sends a date-time as an instant', () => {
    const fields = parseResponseSchema(schemaOf({ when: { type: 'string', format: 'date-time' } }, ['when']))
    const data = buildResponseData(fields, { when: '2027-01-15T09:30' })
    expect(data.when).toBe(new Date('2027-01-15T09:30').toISOString())
  })
})

describe('safeElicitationUrl', () => {
  it('passes web addresses', () => {
    expect(safeElicitationUrl('https://auth.example.com/start?x=1')).toBe('https://auth.example.com/start?x=1')
    expect(safeElicitationUrl('http://localhost:8080/cb')).toBe('http://localhost:8080/cb')
  })

  it('refuses everything else', () => {
    for (const url of ['javascript:alert(1)', 'data:text/html,x', 'file:///etc/passwd', '/relative', '', null, undefined]) {
      expect(safeElicitationUrl(url)).toBeNull()
    }
  })
})

describe('elicitation queue', () => {
  const a = { elicitation_id: 'a' }
  const b = { elicitation_id: 'b' }

  it('keeps requests in arrival order instead of replacing the one on screen', () => {
    const queue = enqueueElicitation(enqueueElicitation([], a), b)
    expect(queue).toEqual([a, b])
  })

  it('ignores a request that is already queued or has no id', () => {
    const queue = [a]
    expect(enqueueElicitation(queue, { elicitation_id: 'a' })).toBe(queue)
    expect(enqueueElicitation(queue, {})).toBe(queue)
    expect(enqueueElicitation(queue, null)).toBe(queue)
  })

  it('settles one request and leaves the rest', () => {
    expect(settleElicitation([a, b], 'a')).toEqual([b])
    expect(settleElicitation([a, b], 'b')).toEqual([a])
  })

  it('returns the same queue when nothing matched', () => {
    const queue = [a]
    expect(settleElicitation(queue, 'zzz')).toBe(queue)
    expect(settleElicitation(queue, undefined)).toBe(queue)
    expect(settleElicitation(null, 'a')).toEqual([])
  })
})
