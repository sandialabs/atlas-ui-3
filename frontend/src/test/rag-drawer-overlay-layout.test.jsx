/**
 * The Data Sources drawer must be an overlay at every breakpoint (issue #1037).
 *
 * It used to become an in-flow flex child on desktop (`lg:relative lg:w-96`)
 * and vanish from the layout when closed (`lg:hidden`), so opening it shoved
 * the whole chat area 384px to the right and closing it let it jump back.
 * The drawer now stays `fixed` and out of document flow at every width: it
 * slides over the chat, and a backdrop (also at desktop widths) closes it.
 *
 * jsdom has no CSS engine, so these tests pin the class strings Tailwind
 * resolves at each breakpoint; the visual geometry itself was verified in a
 * browser during #1037 (header bounding box identical open vs closed).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import RagPanel from '../components/RagPanel'
import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'

vi.mock('../contexts/ChatContext')
vi.mock('../contexts/MarketplaceContext')

function setup({ isOpen = true } = {}) {
  useChat.mockReturnValue({
    ragSources: [],
    selectedDataSources: new Set(),
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
  let toggle

  beforeEach(() => {
    vi.clearAllMocks()
    // The header toggle the drawer returns focus to on close.
    toggle = document.createElement('button')
    toggle.id = 'rag-drawer-toggle'
    document.body.appendChild(toggle)
  })

  afterEach(() => {
    toggle.remove()
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

  it('moves focus into the drawer when it opens', () => {
    setup({ isOpen: true })

    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Close data sources drawer' })
    )
  })

  it('returns focus to the header toggle when it closes', () => {
    const { rerender } = setup({ isOpen: true })

    rerender(<RagPanel isOpen={false} onClose={vi.fn()} />)
    expect(document.activeElement).toBe(toggle)
  })

  it('leaves focus alone when mounted closed', () => {
    // The toggle-focus effect must key on a real open -> closed transition,
    // not fire for a drawer that never opened.
    setup({ isOpen: false })

    expect(document.activeElement).toBe(document.body)
  })
})
