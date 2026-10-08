/**
 * The Data Sources drawer must be an overlay at every breakpoint (issue #1037).
 *
 * It used to become an in-flow flex child on desktop (`lg:relative lg:w-96`)
 * and vanish from the layout when closed (`lg:hidden`), so opening it shoved
 * the whole chat area 384px to the right and closing it let it jump back.
 * The drawer now stays `fixed` and out of document flow at every width: it
 * slides over the chat, and a backdrop (also at desktop widths) closes it.
 *
 * As an overlay it is modal: focus enters on open, Tab is trapped inside,
 * and focus returns to the element that had it when the drawer opened.
 *
 * jsdom has no CSS engine, so these tests pin the class strings Tailwind
 * resolves at each breakpoint; the visual geometry itself was verified in a
 * browser during #1037 (header bounding box identical open vs closed, see
 * docs/developer/design-notes/rag-drawer-overlay-2026-10-08.md).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RagPanel from '../components/RagPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

function setup({ isOpen = true, ragSources = [], selectedDataSources = new Set() } = {}) {
  useChat.mockReturnValue({
    ragSources,
    selectedDataSources,
    toggleDataSource: vi.fn(),
    addDataSources: vi.fn(),
    clearDataSources: vi.fn(),
    features: {},
    complianceLevelFilter: null,
    models: [],
    currentModel: null
  })

  useMarketplace.mockReturnValue({
    complianceLevels: [],
    isComplianceAccessible: vi.fn(() => true)
  })

  const onClose = vi.fn()
  const { container, unmount, rerender } = render(
    <RagPanel isOpen={isOpen} onClose={onClose} />
  )
  return { onClose, container, unmount, rerender }
}

describe('RagPanel - drawer overlays instead of reflowing the chat (issue #1037)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps the drawer fixed and out of document flow on desktop', () => {
    setup({ isOpen: true })

    const drawer = screen.getByTestId('rag-drawer')
    expect(drawer.className).toContain('fixed')
    expect(drawer.className).toContain('top-0')
    // The desktop in-flow classes are the regression: `lg:relative` made the
    // drawer a flex child that pushed the chat right by its width.
    expect(drawer.className).not.toContain('lg:relative')
    // Desktop width is still the wider of the two; it just no longer occupies
    // flex row space.
    expect(drawer.className).toContain('lg:w-96')
  })

  it('never hides the drawer from the layout when closed', () => {
    // The drawer always stays mounted, sliding off-screen instead of being
    // unmounted by `lg:hidden` and re-inserted (in flow) on the next open.
    setup({ isOpen: false })

    const drawer = screen.getByTestId('rag-drawer')
    expect(drawer.className).toContain('-translate-x-full')
    expect(drawer.className).not.toContain('lg:hidden')
    expect(drawer.className).not.toContain('lg:relative')
  })

  it('renders no backdrop when closed and one without breakpoint exclusions when open', () => {
    const { rerender } = setup({ isOpen: false })

    expect(screen.queryByTestId('rag-drawer-backdrop')).not.toBeInTheDocument()

    rerender(<RagPanel isOpen={true} onClose={vi.fn()} />)
    const backdrop = screen.getByTestId('rag-drawer-backdrop')
    expect(backdrop.className).toContain('fixed')
    // `lg:hidden` would leave desktop without the click-outside-to-close.
    expect(backdrop.className).not.toContain('lg:hidden')
  })

  it('closes the drawer when the backdrop is clicked', () => {
    const { onClose } = setup({ isOpen: true })

    fireEvent.click(screen.getByTestId('rag-drawer-backdrop'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes the drawer on Escape', () => {
    const { onClose } = setup({ isOpen: true })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('removes the closed drawer from the accessibility tree and tab order', () => {
    // Off-screen but still mounted means the close button would otherwise
    // stay reachable by keyboard and screen reader while invisible.
    const { unmount } = setup({ isOpen: false })
    const drawer = screen.getByTestId('rag-drawer')
    expect(drawer.getAttribute('aria-hidden')).toBe('true')
    expect(drawer.hasAttribute('inert')).toBe(true)
    unmount()

    setup({ isOpen: true })
    const openDrawer = screen.getByTestId('rag-drawer')
    expect(openDrawer.getAttribute('aria-hidden')).toBe('false')
    expect(openDrawer.hasAttribute('inert')).toBe(false)
  })
})

describe('RagPanel - modal semantics of the overlay drawer', () => {
  let opener

  beforeEach(() => {
    vi.clearAllMocks()
    // Whatever the user was interacting with before the drawer opened.
    opener = document.createElement('button')
    document.body.appendChild(opener)
  })

  afterEach(() => {
    opener.remove()
  })

  it('announces itself as a modal dialog named by its heading', () => {
    setup({ isOpen: true })

    const drawer = screen.getByRole('dialog', { name: 'Data Sources' })
    expect(drawer.getAttribute('aria-modal')).toBe('true')
  })

  it('moves focus into the drawer when it opens', () => {
    opener.focus()
    setup({ isOpen: true })

    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Close data sources drawer' })
    )
  })

  it('returns focus to the element that had it when the drawer opened', () => {
    opener.focus()
    const { rerender } = setup({ isOpen: true })
    expect(document.activeElement).not.toBe(opener)

    rerender(<RagPanel isOpen={false} onClose={vi.fn()} />)
    expect(document.activeElement).toBe(opener)
  })

  it('leaves focus alone when mounted closed', () => {
    // The restore branch must key on a drawer that actually opened, not fire
    // for one that never did.
    opener.focus()
    setup({ isOpen: false })

    expect(document.activeElement).toBe(opener)
  })

  it('traps Tab: forward from the last control wraps to the first', () => {
    setup({
      isOpen: true,
      // A source and a selection keep Enable All / Clear All enabled, so the
      // trap has the drawer's full control set to walk.
      ragSources: [{ id: 'docs', label: 'docs', serverName: 'atlas_rag' }],
      selectedDataSources: new Set(['atlas_rag:docs'])
    })

    const drawer = screen.getByRole('dialog', { name: 'Data Sources' })
    const focusables = drawer.querySelectorAll(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
    )
    focusables[focusables.length - 1].focus()
    fireEvent.keyDown(drawer, { key: 'Tab' })

    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Close data sources drawer' })
    )
  })

  it('traps Tab: backward from the first control wraps to the last', () => {
    setup({
      isOpen: true,
      ragSources: [{ id: 'docs', label: 'docs', serverName: 'atlas_rag' }],
      selectedDataSources: new Set(['atlas_rag:docs'])
    })

    const drawer = screen.getByRole('dialog', { name: 'Data Sources' })
    const focusables = drawer.querySelectorAll(
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
    )
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    first.focus()
    fireEvent.keyDown(drawer, { key: 'Tab', shiftKey: true })

    expect(document.activeElement).toBe(last)
  })
})
