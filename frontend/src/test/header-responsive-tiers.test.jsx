/**
 * The header's three shapes (issue #1071).
 *
 * The bar used to switch to its overflow menu at a fixed width (1160px of
 * *header* width) that was picked before the compliance (security level)
 * selector joined the conversation setup group. With the selector on, the
 * setup group is ~150px wider, so at intermediate widths the bar showed
 * controls it had no room for: the trailing buttons were pushed past the
 * right edge of the page and became unreachable.
 *
 * The bar now has three shapes, and which one applies is decided by the
 * header's own measured width and the measured width of what it holds:
 *
 *   full     the setup group and every action button in the bar;
 *   compact  the setup group in the bar, the action buttons in the menu;
 *   minimal  icons only, with the security level in its compact form.
 *
 * These tests pin that behaviour: what is in the bar at each width, that the
 * security level stays visible for as long as it fits, and that the collapse
 * happens before the content runs out of room.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'

vi.mock('../contexts/ChatContext', () => ({ useChat: vi.fn() }))
vi.mock('../contexts/WSContext', () => ({ useWS: vi.fn() }))
vi.mock('../contexts/MarketplaceContext', () => ({ useMarketplace: vi.fn() }))
vi.mock('react-router-dom', () => ({ useNavigate: vi.fn() }))
vi.mock('../components/ui/toastContext', () => ({
  useToast: () => ({ showToast: vi.fn(), success: vi.fn(), error: vi.fn() })
}))

// The workspace switcher is stubbed so the test can see the `showName` prop
// the header passes it: the name follows the bar's width, not the viewport.
vi.mock('../components/WorkspaceSelector', () => ({
  default: ({ showName }) => <div data-testid="workspace-selector" data-show-name={String(showName)} />,
}))

import Header from '../components/Header'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { useNavigate } from 'react-router-dom'
import {
  ACTION_LABELS_MIN_WIDTH,
  COMPACT_ACTIONS_MIN_WIDTH,
  SECURITY_LEVEL_MIN_WIDTH,
} from '../components/Header'

// The widths the header measures. The header element itself, the left group,
// the row of action buttons, and the connection status.
const measured = {
  header: 1400,
  leftGroup: 770,
  actionCluster: 290,
  status: 96,
  headerHeight: 87,
}

const originalGetBoundingClientRect = Element.prototype.getBoundingClientRect
const originalResizeObserver = globalThis.ResizeObserver

let observerCallbacks = []

class FakeResizeObserver {
  constructor(callback) {
    this.callback = callback
    observerCallbacks.push(callback)
  }
  observe() {}
  disconnect() {
    observerCallbacks = observerCallbacks.filter((cb) => cb !== this.callback)
  }
}

// jsdom lays nothing out, so every element reports width 0. Drive the
// measured widths directly instead, keyed by the test id the header puts on
// each measured element.
const widthFor = element => {
  const testId = element.getAttribute?.('data-testid')
  if (testId === 'header-left-group') return measured.leftGroup
  if (testId === 'header-action-buttons') return measured.actionCluster
  if (testId === 'header-status') return measured.status
  if (element.tagName === 'HEADER') return measured.header
  return 0
}

// The bar's height, which the overflow menu hangs off.
const heightFor = element => (element.tagName === 'HEADER' ? measured.headerHeight : 0)

// The width the bar needs for the full shape: the left group, the connection
// status, the gap between them, the action buttons, the room kept for the
// user name, and the bar's own padding.
const fullBarMinWidth = () =>
  measured.leftGroup + measured.status + 16 + measured.actionCluster + 96 + 32

const setContext = ({ chatOver = {} } = {}) => {
  useChat.mockReturnValue({
    user: 'test@test.com',
    agentModeAvailable: true,
    agentModeEnabled: false,
    setAgentModeEnabled: vi.fn(),
    saveMode: 'server',
    setSaveMode: vi.fn(),
    openChat: vi.fn(),
    openChatAsText: vi.fn(),
    messages: [],
    clearChat: vi.fn(),
    features: { rag: true, chat_history: true, workspaces: true, compliance_levels: true },
    selectedDataSources: new Set(),
    selectedTools: new Set(),
    complianceLevelFilter: null,
    setComplianceLevelFilter: vi.fn(),
    ...chatOver,
  })
  useWS.mockReturnValue({ isConnected: true, connectionStatus: 'Connected' })
  useMarketplace.mockReturnValue({
    complianceLevels: [
      { name: 'Public', description: 'Publicly accessible data' },
      { name: 'Internal', description: 'Internal systems' },
    ],
  })
  useNavigate.mockReturnValue(vi.fn())
}

const renderHeader = (props = {}) => render(
  <Header
    ragPanelOpen={false}
    onToggleSidebar={vi.fn()}
    onToggleRag={vi.fn()}
    onToggleFiles={vi.fn()}
    onToggleCanvas={vi.fn()}
    onCloseCanvas={vi.fn()}
    onToggleSettings={vi.fn()}
    {...props}
  />
)

// Re-measure at the current widths, the way a resize would.
const resize = (widths = {}) => {
  Object.assign(measured, widths)
  act(() => {
    observerCallbacks.forEach((cb) => cb())
  })
}

// The action cluster is in the bar when it is not hidden from the layout.
const actionClusterInBar = () => {
  const cluster = screen.getByTestId('header-action-cluster')
  return !cluster.className.includes('invisible')
}
const securityLevelInBar = () => {
  const control = screen.queryByTestId('security-level-control')
  if (!control) return null
  // The control is also rendered inside the overflow menu; the bar's copy is
  // the one that is not inside the menu panel.
  return control.closest('#mobile-menu') ? null : control
}

beforeEach(() => {
  vi.clearAllMocks()
  observerCallbacks = []
  globalThis.ResizeObserver = FakeResizeObserver
  Element.prototype.getBoundingClientRect = function () {
    const width = widthFor(this)
    const height = heightFor(this)
    return { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0 }
  }
  setContext()
})

afterEach(() => {
  globalThis.ResizeObserver = originalResizeObserver
  Element.prototype.getBoundingClientRect = originalGetBoundingClientRect
  cleanup()
})

describe('header shapes', () => {
  it('shows the full bar once the two groups fit side by side', () => {
    measured.header = fullBarMinWidth()
    renderHeader()

    expect(actionClusterInBar()).toBe(true)
    // The save mode and the setup help are part of the setup group.
    expect(screen.getByTestId('save-chat-control')).toBeTruthy()
    expect(screen.getByTestId('conversation-setup-cluster')).toBeTruthy()
  })

  it('moves the action buttons into the menu before the bar runs out of room', () => {
    // One pixel short of the fit: the buttons must go, rather than be pushed
    // off the right edge of the page.
    measured.header = fullBarMinWidth() - 1
    renderHeader()

    expect(actionClusterInBar()).toBe(false)
    expect(screen.getByRole('button', { name: 'Open menu' })).toBeTruthy()
  })

  it('keeps the security level in the bar while the action buttons collapse', () => {
    measured.header = fullBarMinWidth() - 1
    renderHeader()

    const security = securityLevelInBar()
    expect(security).toBeTruthy()
    // The full form: the setup group is still in the bar.
    expect(security.dataset.density).toBeUndefined()
    expect(screen.getByTestId('save-chat-control')).toBeTruthy()
  })

  it('keeps a compact security level in the bar on narrow bars', () => {
    measured.header = COMPACT_ACTIONS_MIN_WIDTH - 1
    renderHeader()

    const security = securityLevelInBar()
    expect(security).toBeTruthy()
    expect(security.dataset.density).toBe('compact')
    // The save mode and the setup help move into the menu.
    expect(screen.queryByTestId('save-chat-control')).toBeNull()
  })

  it('moves the security level into the menu when even the compact form does not fit', () => {
    measured.header = SECURITY_LEVEL_MIN_WIDTH - 1
    renderHeader()

    expect(securityLevelInBar()).toBeNull()
    // It is still reachable: the overflow menu carries the full control.
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    expect(screen.getAllByTestId('security-level-control').length).toBeGreaterThan(0)
  })

  it('orders the shapes so the security level outlives the labels', () => {
    // The security level is the last thing to leave the bar, so its
    // threshold has to be below the label threshold.
    expect(SECURITY_LEVEL_MIN_WIDTH).toBeLessThan(ACTION_LABELS_MIN_WIDTH)
    expect(ACTION_LABELS_MIN_WIDTH).toBeLessThan(COMPACT_ACTIONS_MIN_WIDTH)
  })

  it('keeps the locked guidance reachable in the compact security level', () => {
    // A begun conversation locks the security level. The compact form has no
    // room for the guidance line, so it stays in the accessibility tree
    // (the select points at it) instead of taking space in the bar.
    measured.header = COMPACT_ACTIONS_MIN_WIDTH - 1
    setContext({
      chatOver: {
        activeConversationClassification: { state: 'classified', level: 'Internal' },
        conversationAccepted: true,
      },
    })
    renderHeader()

    const security = securityLevelInBar()
    expect(security).toBeTruthy()
    expect(security.dataset.density).toBe('compact')
    expect(security.dataset.locked).toBe('true')

    const select = security.querySelector('select')
    expect(select.disabled).toBe(true)
    expect(select.value).toBe('Internal')

    const guidanceId = select.getAttribute('aria-describedby')
    const guidance = document.getElementById(guidanceId)
    expect(guidance).toBeTruthy()
    expect(guidance.textContent).toContain('Start a new chat')
    // The guidance is announced, not painted: the compact bar has no room.
    expect(guidance.className).toContain('sr-only')
  })
})

describe('header labels follow the bar width', () => {
  it('drops the text labels when the bar is narrow', () => {
    measured.header = ACTION_LABELS_MIN_WIDTH - 1
    renderHeader()

    expect(screen.getByRole('button', { name: 'New Chat' })).toBeTruthy()
    expect(screen.queryByText('New Chat')).toBeNull()
  })

  it('keeps the text labels when the bar is wide', () => {
    measured.header = ACTION_LABELS_MIN_WIDTH
    renderHeader()

    expect(screen.getByText('New Chat')).toBeTruthy()
  })

  it('shows the workspace name according to the bar width, not the viewport', () => {
    measured.header = ACTION_LABELS_MIN_WIDTH - 1
    renderHeader()
    expect(screen.getByTestId('workspace-selector').dataset.showName).toBe('false')

    resize({ header: ACTION_LABELS_MIN_WIDTH })
    expect(screen.getByTestId('workspace-selector').dataset.showName).toBe('true')
  })

  it('shows the connection status text only when the bar has room', () => {
    measured.header = 500
    renderHeader()
    // The bar hides the label with a class, so the check is on the class:
    // jsdom does not load the stylesheet that would hide the element.
    const label = () => screen.getByTestId('header-status').querySelector('span')
    expect(label().className).toContain('hidden')

    resize({ header: 600 })
    expect(label().className).toContain('inline')
  })
})

describe('the action cluster stays measurable while it is collapsed', () => {
  it('keeps the buttons in the DOM so the bar can measure them', () => {
    measured.header = COMPACT_ACTIONS_MIN_WIDTH - 1
    renderHeader()

    const cluster = screen.getByTestId('header-action-cluster')
    expect(cluster).toBeTruthy()
    // Collapsed, but not removed: the fit test needs its width. `invisible`
    // takes it out of the accessibility tree and the tab order.
    expect(cluster.className).toContain('invisible')
    expect(cluster.className).toContain('absolute')
    expect(screen.getByTestId('header-action-buttons')).toBeTruthy()
  })
})

describe('the overflow menu hangs off the bar', () => {
  it('starts below the bar, whatever height the bar has', () => {
    // The bar's height depends on its shape: the security level control is
    // taller than the icon buttons, so a fixed offset either left a gap or
    // covered the bar's own controls.
    measured.header = COMPACT_ACTIONS_MIN_WIDTH - 1
    measured.headerHeight = 87
    renderHeader()

    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
    const panel = document.getElementById('mobile-menu')
    expect(panel.style.top).toBe('87px')
    expect(panel.style.maxHeight).toBe('calc(100vh - 87px)')
  })
})
