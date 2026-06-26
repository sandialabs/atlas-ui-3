/**
 * Tests for the inline tool-approval row (ToolApprovalMessage) and the shared
 * tool-call collapse behavior in Message, added/refactored in #673.
 *
 * Covers:
 *  - compact vs classic approval rendering
 *  - review-required default visibility (always expanded, ignores the global
 *    persisted-collapse key)
 *  - auto-approved persisted-collapse default logic
 *  - allow_edit=false hides the Edit affordance
 *  - local decision state + duplicate-submit guard (the backend never echoes a
 *    status change, so the component must resolve the badge locally)
 *  - regression: classic-mode (compact off) tool-call details stay collapsible
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import ToolApprovalMessage from '../components/ToolApprovalMessage'
import Message from '../components/Message'
import { useChat } from '../contexts/ChatContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

const baseMessage = {
  tool_call_id: 'call_1',
  tool_name: 'run_python',
  arguments: { code: 'print(1)', label: 'demo' },
  allow_edit: true,
  admin_required: false,
  status: 'pending',
}

const setChat = (overrides = {}) => {
  const sendApprovalResponse = overrides.sendApprovalResponse || vi.fn()
  const updateSettings = overrides.updateSettings || vi.fn()
  useChat.mockReturnValue({
    sendApprovalResponse,
    updateSettings,
    settings: overrides.settings || { autoApproveTools: false },
    // Fields read by Message (tool-call regression block):
    appName: 'Atlas',
    downloadFile: vi.fn(),
    isSynthesizing: false,
  })
  return { sendApprovalResponse, updateSettings }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

afterEach(() => {
  cleanup()
})

describe('ToolApprovalMessage — compact (default) layout', () => {
  it('renders a review-required row with the tool name and Approve/Reject controls', () => {
    setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    expect(screen.getByText('APPROVAL REQUIRED')).toBeInTheDocument()
    expect(screen.getByText('run_python')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('opens review-required arguments expanded even when the global collapse key is set', () => {
    // Auto-approved rows persist this preference; a review-required prompt must
    // never inherit it — the reviewer has to see what they are approving.
    localStorage.setItem('toolApprovalArgsCollapsed', 'true')
    setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    expect(screen.getByText('Input Arguments')).toBeInTheDocument()
    expect(screen.getByText(/print\(1\)/)).toBeInTheDocument()
  })

  it('collapses an auto-approved row by default (informational) and hides the action buttons', () => {
    setChat({ settings: { autoApproveTools: true } })
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    expect(screen.getByText('AUTO-APPROVED')).toBeInTheDocument()
    // Collapsed: arguments panel not rendered, param count shown instead.
    expect(screen.queryByText('Input Arguments')).not.toBeInTheDocument()
    expect(screen.getByText(/2 params/)).toBeInTheDocument()
    // Auto-approved calls run regardless — no manual Approve/Reject.
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
  })

  it('hides the Edit affordance when allow_edit is false', () => {
    setChat()
    render(
      <ToolApprovalMessage message={{ ...baseMessage, allow_edit: false }} compact={true} />
    )

    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })

  it('shows the Edit affordance when allow_edit is true', () => {
    setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('records the decision locally on approve, swaps to the resolved badge, and guards against a second submit', () => {
    const { sendApprovalResponse } = setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))

    expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
    expect(sendApprovalResponse).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'tool_approval_response',
        tool_call_id: 'call_1',
        approved: true,
      })
    )
    // The backend does not echo a status change, so the component must resolve
    // the terminal badge locally and remove the controls (no duplicate submit).
    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('records a rejection locally with the typed reason', () => {
    const { sendApprovalResponse } = setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    fireEvent.change(screen.getByPlaceholderText(/Rejection reason/), {
      target: { value: 'looks unsafe' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))

    expect(sendApprovalResponse).toHaveBeenCalledWith(
      expect.objectContaining({ approved: false, reason: 'looks unsafe' })
    )
    expect(screen.getByText('REJECTED')).toBeInTheDocument()
    expect(screen.getByText(/looks unsafe/)).toBeInTheDocument()
  })
})

describe('ToolApprovalMessage — classic (compact off) layout', () => {
  it('renders the full-bubble review-required layout with the arguments expanded', () => {
    setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={false} />)

    expect(screen.getByText('APPROVAL REQUIRED')).toBeInTheDocument()
    expect(screen.getByText('Tool Arguments')).toBeInTheDocument()
    expect(screen.getByText(/print\(1\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument()
  })

  it('resolves the terminal badge locally after approve in classic mode too', () => {
    const { sendApprovalResponse } = setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={false} />)

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))

    expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
  })
})

describe('Message — tool-call collapse is shared across compact/classic (regression)', () => {
  const toolCall = {
    role: 'assistant',
    type: 'tool_call',
    tool_name: 'run_python',
    server_name: 'python',
    status: 'completed',
    arguments: { code: 'print(1)' },
    result: 'done',
    timestamp: '2026-06-25T00:00:00Z',
  }

  const expectCollapsibleDetails = () => {
    // Default collapsed: no Input Arguments until the header is clicked.
    expect(screen.queryByText('Input Arguments')).not.toBeInTheDocument()
    fireEvent.click(screen.getByText('SUCCESS'))
    expect(screen.getByText('Input Arguments')).toBeInTheDocument()
  }

  it('keeps tool-call details collapsible in compact mode', () => {
    setChat({ settings: { compactMessages: true } })
    render(<Message message={toolCall} />)
    expectCollapsibleDetails()
  })

  it('keeps tool-call details collapsible in classic mode (compact off)', () => {
    // Pre-#673 the classic layout was collapsible; the compact toggle only
    // controls chrome, so this must remain true with compact off.
    setChat({ settings: { compactMessages: false } })
    render(<Message message={toolCall} />)
    expectCollapsibleDetails()
  })
})
