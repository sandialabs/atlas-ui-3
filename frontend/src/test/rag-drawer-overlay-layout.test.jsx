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
import { FOCUSABLE_SELECTOR } from '../utils/focusTrap'

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

  it('closes the drawer via the X button in its header', () => {
    const { onClose } = setup({ isOpen: true })

    fireEvent.click(screen.getByRole('button', { name: 'Close data sources drawer' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('does not close anything on Escape while the drawer is closed', () => {
    const { onClose } = setup({ isOpen: false })

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
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
  let otherDialog

  beforeEach(() => {
    vi.clearAllMocks()
    // Whatever the user was interacting with before the drawer opened.
    opener = document.createElement('button')
    document.body.appendChild(opener)
  })

  afterEach(() => {
    opener.remove()
    // Cleanup here rather than at the end of each test, so one failing
    // assertion does not leave a fake dialog behind and break the tests
    // after it.
    if (otherDialog) {
      otherDialog.remove()
      otherDialog = null
    }
  })

  // Mirrors the real Tools and Settings modal, which carries
  // role="dialog" aria-modal="true" -- the topmost-dialog check keys on both.
  const addOtherDialog = ({ prepend = false } = {}) => {
    otherDialog = document.createElement('div')
    otherDialog.setAttribute('role', 'dialog')
    otherDialog.setAttribute('aria-modal', 'true')
    const insideOther = document.createElement('button')
    otherDialog.appendChild(insideOther)
    if (prepend) {
      document.body.insertBefore(otherDialog, document.body.firstChild)
    } else {
      document.body.appendChild(otherDialog)
    }
    return insideOther
  }

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

  it('walks the real closed -> open -> closed path with focus intact', () => {
    // The app always mounts the drawer closed and opens it from the header
    // toggle; the whole cycle must hand focus back where it started.
    opener.focus()
    const { rerender } = setup({ isOpen: false })
    expect(document.activeElement).toBe(opener)

    rerender(<RagPanel isOpen={true} onClose={vi.fn()} />)
    const closeButton = screen.getByRole('button', { name: 'Close data sources drawer' })
    expect(document.activeElement).toBe(closeButton)

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
    const focusables = drawer.querySelectorAll(FOCUSABLE_SELECTOR)
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
    const focusables = drawer.querySelectorAll(FOCUSABLE_SELECTOR)
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    first.focus()
    fireEvent.keyDown(drawer, { key: 'Tab', shiftKey: true })

    expect(document.activeElement).toBe(last)
  })

  it('pulls stray focus back into the drawer: the trap listens on document', () => {
    // Focus can legitimately leave the drawer while it is up (e.g. "Clear
    // All" disabled itself and dropped focus to <body>); from there a Tab
    // must return into the drawer, not walk the covered header controls.
    setup({ isOpen: true })

    const drawer = screen.getByRole('dialog', { name: 'Data Sources' })
    const focusables = drawer.querySelectorAll(FOCUSABLE_SELECTOR)
    // document.body.focus() is a no-op in jsdom; blur() is what actually
    // moves activeElement to <body>.
    screen.getByRole('button', { name: 'Close data sources drawer' }).blur()
    expect(drawer.contains(document.activeElement)).toBe(false)

    fireEvent.keyDown(document.body, { key: 'Tab' })
    expect(document.activeElement).toBe(focusables[0])

    // And the same from <body> with Shift+Tab lands on the last control.
    screen.getByRole('button', { name: 'Close data sources drawer' }).blur()
    fireEvent.keyDown(document.body, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(focusables[focusables.length - 1])
  })

  it('stands down while focus is inside a different dialog', () => {
    // The Tools and Settings modal can be layered on top of an open drawer;
    // its own trap owns Tab while focus is in there.
    setup({ isOpen: true })

    const insideOther = addOtherDialog()
    insideOther.focus()

    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(insideOther)
  })

  it('stands down for an earlier-in-document modal too: ownership is not focus, not first-match', () => {
    // The ownership rule is "last aria-modal dialog in the document wins"
    // (App renders SettingsPanel after RagPanel). A modal that happens to
    // sit BEFORE the drawer in document order does not take ownership.
    setup({ isOpen: true })

    const insideOther = addOtherDialog({ prepend: true })

    fireEvent.keyDown(document, { key: 'Tab' })
    // The drawer is still the topmost modal: the trap pulls focus back in.
    expect(document.activeElement).not.toBe(insideOther)
    expect(screen.getByRole('dialog', { name: 'Data Sources' }).contains(document.activeElement)).toBe(true)
  })

  it('does not close on Escape while focus is inside a different dialog', () => {
    // A drawer left open beneath the Tools and Settings modal must not
    // swallow the modal's Escape: whichever dialog has focus owns Escape.
    const { onClose } = setup({ isOpen: true })

    const insideOther = addOtherDialog()
    insideOther.focus()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()

    // Clicking non-focusable text in the modal drops focus to <body>: with
    // no focus to inspect, ownership must still follow the topmost modal
    // dialog, not fall back to the drawer.
    insideOther.blur()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('lets the Escape key through to bubble-phase listeners when standing down', () => {
    // The drawer's own Escape listener sits in the capture phase and stops
    // propagation -- but ONLY once its stand-down predicate has passed. This
    // is the regression: stopping propagation first swallowed the key before
    // the Settings modal's bubble-phase handler could see it, so Escape
    // closed neither overlay.
    const { onClose } = setup({ isOpen: true })

    const insideOther = addOtherDialog()
    insideOther.focus()

    let bubbleListenerSawEscape = false
    const bubbleListener = (event) => {
      if (event.key === 'Escape') bubbleListenerSawEscape = true
    }
    document.addEventListener('keydown', bubbleListener)
    try {
      fireEvent.keyDown(document, { key: 'Escape' })
      expect(bubbleListenerSawEscape).toBe(true)
      expect(onClose).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', bubbleListener)
    }
  })

  it('skips focus restore when the drawer closed under a different dialog', () => {
    // If the drawer is dismissed while the user is in a modal layered on top
    // of it, restoring saved focus would yank focus behind that modal's
    // backdrop.
    opener.focus()
    const { rerender } = setup({ isOpen: true })

    const insideOther = addOtherDialog()
    insideOther.focus()

    rerender(<RagPanel isOpen={false} onClose={vi.fn()} />)
    expect(document.activeElement).toBe(insideOther)
  })
})
