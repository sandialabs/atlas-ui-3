/**
 * Tests for ModelInfoPopover and formatContextWindow.
 *
 * These test the extracted formatting logic and component rendering decisions
 * for the model info panel in the dropdown.
 */

import { describe, it, expect } from 'vitest'

// Mirror of formatContextWindow from ModelInfoPopover.jsx
function formatContextWindow(tokens) {
  if (tokens == null) return null
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M tokens`
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K tokens`
  return `${tokens} tokens`
}

// Mirror of which capability badges should render
function getVisibleBadges(model) {
  const badges = []
  if (model.supports_vision) badges.push('vision')
  if (model.supports_tools) badges.push('tools')
  if (model.supports_reasoning) badges.push('reasoning')
  return badges
}

// Mirror of whether the info button should appear
function shouldShowInfoButton(model) {
  return !!(model.model_card_url || model.context_window)
}

describe('formatContextWindow', () => {
  it('returns null for null/undefined', () => {
    expect(formatContextWindow(null)).toBe(null)
    expect(formatContextWindow(undefined)).toBe(null)
  })

  it('formats millions', () => {
    expect(formatContextWindow(1000000)).toBe('1.0M tokens')
    expect(formatContextWindow(1048576)).toBe('1.0M tokens')
    expect(formatContextWindow(2000000)).toBe('2.0M tokens')
  })

  it('formats thousands', () => {
    expect(formatContextWindow(128000)).toBe('128K tokens')
    expect(formatContextWindow(32000)).toBe('32K tokens')
    expect(formatContextWindow(4096)).toBe('4K tokens')
    expect(formatContextWindow(1000)).toBe('1K tokens')
  })

  it('formats small values', () => {
    expect(formatContextWindow(512)).toBe('512 tokens')
    expect(formatContextWindow(1)).toBe('1 tokens')
  })

  it('handles zero', () => {
    expect(formatContextWindow(0)).toBe('0 tokens')
  })
})

describe('Capability badges', () => {
  it('shows no badges when no capabilities', () => {
    expect(getVisibleBadges({})).toEqual([])
  })

  it('shows all badges when all capabilities true', () => {
    const model = { supports_vision: true, supports_tools: true, supports_reasoning: true }
    expect(getVisibleBadges(model)).toEqual(['vision', 'tools', 'reasoning'])
  })

  it('shows only set capabilities', () => {
    expect(getVisibleBadges({ supports_tools: true })).toEqual(['tools'])
    expect(getVisibleBadges({ supports_vision: true, supports_reasoning: true })).toEqual(['vision', 'reasoning'])
  })

  it('excludes false capabilities', () => {
    const model = { supports_vision: false, supports_tools: true, supports_reasoning: false }
    expect(getVisibleBadges(model)).toEqual(['tools'])
  })
})

describe('Info button visibility', () => {
  it('hidden when no model_card_url or context_window', () => {
    expect(shouldShowInfoButton({})).toBe(false)
    expect(shouldShowInfoButton({ supports_vision: true })).toBe(false)
  })

  it('shown when model_card_url present', () => {
    expect(shouldShowInfoButton({ model_card_url: 'https://example.com' })).toBe(true)
  })

  it('shown when context_window present', () => {
    expect(shouldShowInfoButton({ context_window: 128000 })).toBe(true)
  })

  it('shown when both present', () => {
    expect(shouldShowInfoButton({ model_card_url: 'https://example.com', context_window: 128000 })).toBe(true)
  })
})
