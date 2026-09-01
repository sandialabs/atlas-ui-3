/**
 * Tests for the Active Tools strip above the chat input (EnabledToolsIndicator),
 * added for #870.
 *
 * Covers:
 *  - the strip is a single row on every viewport: the pill row never wraps
 *    (flex-nowrap + horizontal scroll) and the auto-approve toggle sits
 *    outside that scroll region so it is always reachable
 *  - compact mode still caps the visible pills and the +N more toggle expands,
 *    and expanding visibly reveals them by wrapping the row (#876)
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

    // flex-1 + min-w-0 is what lets the nowrap row shrink to the space left
    // by the pinned controls instead of pushing them off screen (review).
    expect(pillRow.className).toContain('flex-1')
    expect(pillRow.className).toContain('min-w-0')

    // The scroll region is keyboard-focusable — scrollbar-hide leaves no
    // scrollbar, so keyboard users need another way to reach the overflow.
    expect(pillRow.getAttribute('tabindex')).toBe('0')
    expect(pillRow.getAttribute('aria-label')).toMatch(/scroll/i)

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

  it('keeps the +N more toggle pinned next to auto-approve, outside the scroll region (review)', () => {
    const keys = ['s_a', 's_b', 's_c', 's_d', 's_e', 's_f', 's_g']
    setChat({ selectedTools: new Set(keys) })
    render(<EnabledToolsIndicator />)

    const more = screen.getByRole('button', { name: /2 more/i })
    // The +N more button is the only control that reveals hidden tools, so it
    // must be visible even when the pill row has scrolled its overflow.
    expect(more.closest('.overflow-x-auto')).toBeNull()
    expect(more.className).toContain('flex-shrink-0')
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

  it('wraps the pill row when expanded so the extra tools are actually visible (#876)', () => {
    const keys = ['s_a', 's_b', 's_c', 's_d', 's_e', 's_f', 's_g']
    setChat({ selectedTools: new Set(keys) })
    render(<EnabledToolsIndicator />)

    const more = screen.getByRole('button', { name: /2 more/i })
    expect(more.getAttribute('aria-expanded')).toBe('false')

    // Collapsed the row is a single horizontally scrolling line (#870).
    let pillRow = screen.getByText('a').closest('div[tabindex="0"]')
    expect(pillRow.className).toContain('flex-nowrap')
    expect(pillRow.className).toContain('overflow-x-auto')

    fireEvent.click(more)

    // Expanded it must wrap: with flex-nowrap the newly rendered pills only
    // extended a row that already scrolled horizontally, so clicking
    // "+N more" changed nothing the user could see (#876).
    pillRow = screen.getByText('a').closest('div[tabindex="0"]')
    expect(pillRow.className).toContain('flex-wrap')
    expect(pillRow.className).not.toContain('flex-nowrap')
    expect(pillRow.className).not.toContain('overflow-x-auto')

    // The expanded block is height-capped and scrolls vertically instead of
    // pushing the composer down when many tools are selected.
    expect(pillRow.className).toContain('max-h-24')
    expect(pillRow.className).toContain('overflow-y-auto')
    expect(pillRow.getAttribute('aria-label')).toMatch(/vertically/i)

    expect(screen.getByRole('button', { name: /show less/i }).getAttribute('aria-expanded')).toBe('true')

    // And collapsing restores the single-row behaviour.
    fireEvent.click(screen.getByRole('button', { name: /show less/i }))
    pillRow = screen.getByText('a').closest('div[tabindex="0"]')
    expect(pillRow.className).toContain('flex-nowrap')
    expect(screen.queryByText('f')).toBeNull()
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