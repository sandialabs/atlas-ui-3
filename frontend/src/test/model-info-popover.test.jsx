/**
 * Tests for ModelInfoPopover component and formatContextWindow.
 *
 * Tests the real component rendering, conditional display logic,
 * and the exported formatting utility.
 */

import { describe, it, expect } from 'vitest'
import React from 'react'
import { render, screen } from '@testing-library/react'
import ModelInfoPopover from '../components/ModelInfoPopover'
import { formatContextWindow } from '../utils/modelUtils'

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

describe('ModelInfoPopover component', () => {
  it('renders nothing when model is null', () => {
    const { container } = render(<ModelInfoPopover model={null} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders context window when provided', () => {
    render(<ModelInfoPopover model={{ context_window: 128000 }} />)
    expect(screen.getByText(/Context:/)).toBeInTheDocument()
    expect(screen.getByText(/128K tokens/)).toBeInTheDocument()
  })

  it('does not render context line when context_window is null', () => {
    render(<ModelInfoPopover model={{ supports_tools: true }} />)
    expect(screen.queryByText(/Context:/)).not.toBeInTheDocument()
  })

  it('renders vision badge when supports_vision is true', () => {
    render(<ModelInfoPopover model={{ supports_vision: true }} />)
    expect(screen.getByText('Vision')).toBeInTheDocument()
  })

  it('renders tools badge when supports_tools is true', () => {
    render(<ModelInfoPopover model={{ supports_tools: true }} />)
    expect(screen.getByText('Tools')).toBeInTheDocument()
  })

  it('renders reasoning badge when supports_reasoning is true', () => {
    render(<ModelInfoPopover model={{ supports_reasoning: true }} />)
    expect(screen.getByText('Reasoning')).toBeInTheDocument()
  })

  it('does not render badges when all capabilities are false', () => {
    render(<ModelInfoPopover model={{ supports_vision: false, supports_tools: false, supports_reasoning: false }} />)
    expect(screen.queryByText('Vision')).not.toBeInTheDocument()
    expect(screen.queryByText('Tools')).not.toBeInTheDocument()
    expect(screen.queryByText('Reasoning')).not.toBeInTheDocument()
  })

  it('renders all badges when all capabilities are true', () => {
    render(<ModelInfoPopover model={{ supports_vision: true, supports_tools: true, supports_reasoning: true }} />)
    expect(screen.getByText('Vision')).toBeInTheDocument()
    expect(screen.getByText('Tools')).toBeInTheDocument()
    expect(screen.getByText('Reasoning')).toBeInTheDocument()
  })

  it('renders model card link when model_card_url is provided', () => {
    render(<ModelInfoPopover model={{ model_card_url: 'https://example.com/model' }} />)
    const link = screen.getByText('View Model Card')
    expect(link).toBeInTheDocument()
    expect(link.closest('a')).toHaveAttribute('href', 'https://example.com/model')
    expect(link.closest('a')).toHaveAttribute('target', '_blank')
    expect(link.closest('a')).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('does not render model card link when model_card_url is absent', () => {
    render(<ModelInfoPopover model={{ context_window: 4096 }} />)
    expect(screen.queryByText('View Model Card')).not.toBeInTheDocument()
  })

  it('renders full model info with all fields', () => {
    render(<ModelInfoPopover model={{
      context_window: 1000000,
      supports_vision: true,
      supports_tools: true,
      supports_reasoning: false,
      model_card_url: 'https://example.com/card',
    }} />)
    expect(screen.getByText(/1.0M tokens/)).toBeInTheDocument()
    expect(screen.getByText('Vision')).toBeInTheDocument()
    expect(screen.getByText('Tools')).toBeInTheDocument()
    expect(screen.queryByText('Reasoning')).not.toBeInTheDocument()
    expect(screen.getByText('View Model Card')).toBeInTheDocument()
  })
})
