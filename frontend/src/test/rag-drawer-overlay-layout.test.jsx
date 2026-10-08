/**
 * The Data Sources drawer must be an overlay at every breakpoint (issue #1037).
 *
 * It used to become an in-flow flex child on desktop (`lg:relative lg:w-96`)
 * and vanish from the layout when closed (`lg:hidden`), so opening it shoved
 * the whole chat area 384px to the right and closing it let it jump back.
 * The drawer now stays `fixed` and out of document flow at every width: it
 * slides over the chat, and a backdrop (also at desktop widths) closes it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
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
  const { container, unmount } = render(<RagPanel isOpen={isOpen} onClose={onClose} />)
  return { onClose, container, unmount }
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

  it('keeps the drawer open without translate and without in-flow classes', () => {
    setup({ isOpen: true })

    const drawer = screen.getByTestId('rag-drawer')
    expect(drawer.className).toContain('translate-x-0')
    expect(drawer.className).not.toContain('-translate-x-full')
    expect(drawer.className).not.toContain('lg:hidden')
  })

  it('renders the backdrop at desktop widths too', () => {
    setup({ isOpen: true })

    const backdrop = screen.getByTestId('rag-drawer-backdrop')
    expect(backdrop.className).toContain('fixed')
    expect(backdrop.className).not.toContain('lg:hidden')
  })

  it('closes the drawer when the backdrop is clicked', () => {
    const { onClose } = setup({ isOpen: true })

    fireEvent.click(screen.getByTestId('rag-drawer-backdrop'))
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
