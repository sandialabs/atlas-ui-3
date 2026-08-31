/**
 * Tests for the Active Tools strip above the chat input (EnabledToolsIndicator),
 * added for #870.
 *
 * Covers:
 *  - the strip is a single row on every viewport: the pill row never wraps
 *    (flex-nowrap + horizontal scroll) and the auto-approve toggle sits
 *    outside that scroll region so it is always reachable
 *  - compact mode still caps the visible pills and the +N more toggle expands
 *  - toggling auto-approve flips the persisted setting
 *  - renders nothing when no tools are selected
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import EnabledToolsIndicator from '../components/EnabledToolsIndicator'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const setChat = (overrides = {}) => {
  const toggleTool = overrides.toggleTool || vi.fn()
  const updateSettings = overrides.updateSettings || vi.fn()
  useChat.mockReturnValue({
    selectedTools: overrides.selectedTools || new Set(),
    toggleTool,
    tools: overrides.tools || [],
    settings: { autoApproveTools: false, ...overrides.settings },
    updateSettings,
  })
  return { toggleTool, updateSettings }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
})

describe('EnabledToolsIndicator (#870 single-row strip)', () => {
  it('renders nothing when no tools are selected', () => {
    setChat()
    const { container } = render(<EnabledToolsIndicator />)
    expect(container.firstChild).toBeNull()
  })

  it('never wraps the pill row: it scrolls horizontally instead (#870)', () => {
    setChat({ selectedTools: new Set(['readtool']) })
    const { container } = render(<EnabledToolsIndicator />)

    // The pill row is the scroll container: nowrap + overflow-x, scrollbar hidden.
    const pillRow = screen.getByText('readtool').closest('.overflow-x-auto')
    expect(pillRow).not.toBeNull()
    expect(pillRow.className).toContain('flex-nowrap')
    expect(pillRow.className).toContain('overflow-x-auto')
    expect(pillRow.className).toContain('scrollbar-hide')
    expect(pillRow.className).not.toContain('flex-wrap')

    // Pills cannot shrink below their content — they overflow into the scroll.
    const pill = screen.getByText('readtool').closest('.rounded')
    expect(pill.className).toContain('flex-shrink-0')

    // The strip root itself must not wrap either.
    const strip = container.firstChild
    expect(strip.className).toContain('items-center')
    expect(strip.className).not.toContain('flex-wrap')
  })

  it('keeps the auto-approve toggle outside the horizontally scrolled region (#870)', () => {
    setChat({ selectedTools: new Set(['readtool', 'writetool']) })
    render(<EnabledToolsIndicator />)

    const toggle = screen.getByRole('button', { name: /auto-approve off/i })
    // If the toggle lived inside the scroll container it could be scrolled out
    // of view on a narrow screen; it must be a pinned sibling instead.
    expect(toggle.closest('.overflow-x-auto')).toBeNull()
    expect(toggle.className).toContain('flex-shrink-0')
    expect(toggle.getAttribute('aria-pressed')).toBe('false')
  })

  it('caps visible pills at the compact threshold and expands on demand', () => {
    const keys = ['s_a', 's_b', 's_c', 's_d', 's_e', 's_f', 's_g']
    setChat({ selectedTools: new Set(keys) })
    render(<EnabledToolsIndicator />)

    for (const name of ['a', 'b', 'c', 'd', 'e']) {
      expect(screen.getByText(name)).toBeTruthy()
    }
    expect(screen.queryByText('f')).toBeNull()
    expect(screen.getByRole('button', { name: /2 more/i })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /2 more/i }))
    expect(screen.getByText('f')).toBeTruthy()
    expect(screen.getByText('g')).toBeTruthy()
    expect(screen.getByRole('button', { name: /show less/i })).toBeTruthy()
  })

  it('removes a tool when its pill X is clicked', () => {
    const { toggleTool } = setChat({ selectedTools: new Set(['readtool']) })
    render(<EnabledToolsIndicator />)
    fireEvent.click(screen.getByTitle('Remove readtool'))
    expect(toggleTool).toHaveBeenCalledWith('readtool')
  })

  it('toggles auto-approve via the pinned button', () => {
    const { updateSettings } = setChat({
      selectedTools: new Set(['readtool']),
      settings: { autoApproveTools: true },
    })
    render(<EnabledToolsIndicator />)

    const toggle = screen.getByRole('button', { name: /auto-approve on/i })
    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(toggle)
    expect(updateSettings).toHaveBeenCalledWith({ autoApproveTools: false })
  })
})