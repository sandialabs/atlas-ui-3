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
import { useToast } from '../components/ui/toastContext'

vi.mock('../contexts/ChatContext', () => ({
  useChat: vi.fn(),
}))

vi.mock('../components/ui/toastContext', () => ({
  useToast: vi.fn(),
}))

const baseMessage = {
  tool_call_id: 'call_1',
  tool_name: 'run_python',
  arguments: { code: 'print(1)', label: 'demo' },
  allow_edit: true,
  admin_required: false,
  status: 'pending',
}

const toastError = vi.fn()

const setChat = (overrides = {}) => {
  // Default to a successful send so approve/reject tests persist terminal state
  // the same way a live WebSocket does (`sendMessage` returns true when open).
  const sendApprovalResponse = overrides.sendApprovalResponse || vi.fn().mockReturnValue(true)
  const updateSettings = overrides.updateSettings || vi.fn()
  const updateToolResult = overrides.updateToolResult || vi.fn()
  useChat.mockReturnValue({
    sendApprovalResponse,
    updateSettings,
    updateToolResult,
    settings: overrides.settings || { autoApproveTools: false },
    // Fields read by Message (tool-call regression block):
    appName: 'Atlas',
    downloadFile: vi.fn(),
    isSynthesizing: false,
  })
  return { sendApprovalResponse, updateSettings, updateToolResult }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  useToast.mockReturnValue({ error: toastError, success: vi.fn(), info: vi.fn(), dismiss: vi.fn() })
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

  it('renders nothing for an auto-approved call (#762) but still sends the approval', async () => {
    // The row said "we approved a thing we are about to report anyway" — the
    // tool_call row names the same tool a moment later. The component still
    // mounts because it owns the auto-approval effect.
    vi.useFakeTimers()
    try {
      // `sendApprovalResponse` returns true when the WebSocket is open; the
      // auto-approval effect only persists `auto_approved` after a successful
      // send, so the mock must report a successful send.
      const sendApprovalResponse = vi.fn().mockReturnValue(true)
      const { updateToolResult } = setChat({
        settings: { autoApproveTools: true },
        sendApprovalResponse,
      })
      const { container } = render(<ToolApprovalMessage message={baseMessage} compact={true} />)

      expect(container).toBeEmptyDOMElement()
      expect(screen.queryByText('AUTO-APPROVED')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()

      await vi.advanceTimersByTimeAsync(200)
      // The decision is recorded on the message only after the send succeeds,
      // so rendering no longer depends on the live setting.
      expect(updateToolResult).toHaveBeenCalledWith('call_1', { auto_approved: true })
      expect(sendApprovalResponse).toHaveBeenCalledWith(
        expect.objectContaining({ tool_call_id: 'call_1', approved: true })
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('marks auto_approved false and toasts when the WebSocket is down during auto-approval', async () => {
    // If the socket dropped during the 100ms delay, `sendApprovalResponse`
    // returns false. Persisting `auto_approved: true` would leave the row
    // stuck hidden with no approval in flight. Instead persist false so
    // `resolveAutoApproved` un-hides the review row for a manual retry, and
    // surface a toast so the failure is not silent.
    vi.useFakeTimers()
    try {
      const sendApprovalResponse = vi.fn().mockReturnValue(false)
      const { updateToolResult } = setChat({
        settings: { autoApproveTools: true },
        sendApprovalResponse,
      })
      render(<ToolApprovalMessage message={baseMessage} compact={true} />)

      await vi.advanceTimersByTimeAsync(200)

      expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
      expect(updateToolResult).toHaveBeenCalledWith('call_1', { auto_approved: false })
      expect(toastError).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not re-send auto-approval after a successful send when deps churn', async () => {
    // After a successful auto-send, status stays `pending` until tool_start.
    // `sendApprovalResponse` is not memoized, so a parent re-render changes
    // its identity and would re-run the effect without the auto_approved /
    // ref gate — sending a second tool_approval_response for the same call.
    vi.useFakeTimers()
    try {
      const sendApprovalResponse = vi.fn().mockReturnValue(true)
      const updateToolResult = vi.fn()
      setChat({
        settings: { autoApproveTools: true },
        sendApprovalResponse,
        updateToolResult,
      })
      const { rerender } = render(
        <ToolApprovalMessage message={baseMessage} compact={true} />
      )

      await vi.advanceTimersByTimeAsync(200)
      expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
      expect(updateToolResult).toHaveBeenCalledWith('call_1', { auto_approved: true })

      // Simulate the store patch + a new sendApprovalResponse identity.
      const sendApprovalResponse2 = vi.fn().mockReturnValue(true)
      setChat({
        settings: { autoApproveTools: true },
        sendApprovalResponse: sendApprovalResponse2,
        updateToolResult,
      })
      rerender(
        <ToolApprovalMessage
          message={{ ...baseMessage, auto_approved: true }}
          compact={true}
        />
      )
      await vi.advanceTimersByTimeAsync(200)

      expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
      expect(sendApprovalResponse2).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not patch terminal status when a manual approve send fails', () => {
    const sendApprovalResponse = vi.fn().mockReturnValue(false)
    const { updateToolResult } = setChat({ sendApprovalResponse })
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))

    expect(sendApprovalResponse).toHaveBeenCalledTimes(1)
    expect(updateToolResult).not.toHaveBeenCalled()
    // Controls stay so the user can retry after reconnect.
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument()
    expect(toastError).toHaveBeenCalled()
  })

  it('keeps a persisted auto-approved call hidden after the setting is toggled off', () => {
    // Regression for the live-settings defect: the approval decision is history
    // and must not re-render differently when the setting changes later.
    setChat({ settings: { autoApproveTools: false } })
    const { container } = render(
      <ToolApprovalMessage
        message={{ ...baseMessage, status: 'completed', auto_approved: true }}
        compact={true}
      />
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('shows the APPROVAL REQUIRED row for an admin-required call even with auto-approve on', () => {
    setChat({ settings: { autoApproveTools: true } })
    render(
      <ToolApprovalMessage message={{ ...baseMessage, admin_required: true }} compact={true} />
    )

    expect(screen.getByText('APPROVAL REQUIRED')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Approve/ })).toBeInTheDocument()
  })

  it('no longer hosts the auto-approve toggle — it lives on the Active Tools strip', () => {
    setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    expect(screen.queryByRole('button', { name: /Auto-approve/ })).not.toBeInTheDocument()
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

  it('persists the decision to the global store so the controls stay gone after a remount', () => {
    // The message list keys by array index, so an earlier message
    // appearing/collapsing remounts this row and wipes local state. The
    // decision must be written to the message (keyed by tool_call_id) so a
    // remount renders the terminal badge, not a fresh Approve/Reject row.
    const { updateToolResult } = setChat()
    const { unmount } = render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))
    expect(updateToolResult).toHaveBeenCalledWith('call_1', {
      status: 'approved',
      auto_approved: false,
    })

    // Simulate the remount with the patched message the store now holds.
    unmount()
    render(<ToolApprovalMessage message={{ ...baseMessage, status: 'approved' }} compact={true} />)

    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('stays resolved when the backend overwrites the row status to completed after the tool runs', () => {
    // The execution lifecycle (tool_start/tool_complete) reuses the same
    // tool_call_id, so it patches this approval row's status to 'completed'.
    // That still means the call was approved — the controls must not reappear.
    setChat()
    const { rerender } = render(<ToolApprovalMessage message={baseMessage} compact={true} />)
    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))

    rerender(<ToolApprovalMessage message={{ ...baseMessage, status: 'completed' }} compact={true} />)

    expect(screen.getByText('APPROVED')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('records a rejection locally with the typed reason', () => {
    const { sendApprovalResponse, updateToolResult } = setChat()
    render(<ToolApprovalMessage message={baseMessage} compact={true} />)

    fireEvent.change(screen.getByPlaceholderText(/Rejection reason/), {
      target: { value: 'looks unsafe' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))

    expect(sendApprovalResponse).toHaveBeenCalledWith(
      expect.objectContaining({ approved: false, reason: 'looks unsafe' })
    )
    expect(updateToolResult).toHaveBeenCalledWith('call_1', {
      status: 'rejected',
      rejection_reason: 'looks unsafe',
      auto_approved: false,
    })
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

  // Compact rows label the outcome on a glyph; classic rows keep the text pill.
  const expectCollapsibleDetails = (header) => {
    // Default collapsed: no Input Arguments until the header is clicked.
    expect(screen.queryByText('Input Arguments')).not.toBeInTheDocument()
    fireEvent.click(header())
    expect(screen.getByText('Input Arguments')).toBeInTheDocument()
  }

  it('keeps tool-call details collapsible in compact mode', () => {
    setChat({ settings: { compactMessages: true } })
    render(<Message message={toolCall} />)
    expectCollapsibleDetails(() => screen.getByLabelText('SUCCESS'))
  })

  it('keeps tool-call details collapsible in classic mode (compact off)', () => {
    // Pre-#673 the classic layout was collapsible; the compact toggle only
    // controls chrome, so this must remain true with compact off.
    setChat({ settings: { compactMessages: false } })
    render(<Message message={toolCall} />)
    expectCollapsibleDetails(() => screen.getByText('SUCCESS'))
  })

  it('collapses the compact row to one line: no status pill, server name, or param count (#762)', () => {
    setChat({ settings: { compactMessages: true } })
    render(<Message message={toolCall} />)

    expect(screen.getByText('run_python')).toBeInTheDocument()
    // Outcome is a labelled glyph, not a text pill.
    expect(screen.queryByText('SUCCESS')).not.toBeInTheDocument()
    expect(screen.getByLabelText('SUCCESS')).toHaveTextContent('✓')
    expect(screen.queryByText('(python)')).not.toBeInTheDocument()
    expect(screen.queryByText(/1 param/)).not.toBeInTheDocument()

    // The server name is not lost — it moves into the expanded detail.
    fireEvent.click(screen.getByLabelText('SUCCESS'))
    expect(screen.getByText('Server: python')).toBeInTheDocument()
  })

  it('marks a failed call with a red ✗ glyph', () => {
    setChat({ settings: { compactMessages: true } })
    render(<Message message={{ ...toolCall, status: 'failed' }} />)

    expect(screen.getByLabelText('FAILED')).toHaveTextContent('✗')
  })

  it('renders a stopped call neutrally rather than as an error', () => {
    // A user's own Stop is not a tool failure: it must not read as a red
    // FAILED row under "Error Details" (#755).
    setChat({ settings: { compactMessages: true } })
    const stopped = {
      ...toolCall,
      status: 'interrupted',
      result: 'Stopped before the tool result was recorded.',
    }
    render(<Message message={stopped} />)

    const glyph = screen.getByLabelText('STOPPED')
    expect(glyph).toHaveTextContent('⏹')
    expect(glyph.className).not.toMatch(/red/)
    expect(screen.queryByLabelText('FAILED')).not.toBeInTheDocument()

    fireEvent.click(glyph)
    expect(screen.getByText('Stopped Before Result')).toBeInTheDocument()
    expect(screen.queryByText('Error Details')).not.toBeInTheDocument()
  })

  it('labels the active spinner for screen readers in compact and classic modes', () => {
    // The active spinner replaces the outcome glyph while a tool is running.
    // Without an aria-label the spinning state is announced as nothing, so
    // the spinner carries the same statusLabel as the glyph (Copilot review).
    const active = { ...toolCall, status: 'calling', result: undefined }
    setChat({ settings: { compactMessages: true } })
    const { rerender } = render(<Message message={active} />)
    expect(screen.getByLabelText('CALLING')).toBeInTheDocument()

    setChat({ settings: { compactMessages: false } })
    rerender(<Message message={active} />)
    expect(screen.getByLabelText('CALLING')).toBeInTheDocument()

    const inProgress = { ...toolCall, status: 'in_progress', result: undefined }
    setChat({ settings: { compactMessages: true } })
    rerender(<Message message={inProgress} />)
    expect(screen.getByLabelText('IN PROGRESS')).toBeInTheDocument()
  })

  it('renders no transcript row for an auto-approved approval request (#762)', () => {
    // The hidden wrapper keeps ToolApprovalMessage mounted (it owns the
    // auto-approval effect) while contributing no height or space-y gap.
    setChat({ settings: { compactMessages: true, autoApproveTools: true } })
    const { container } = render(
      <Message
        message={{
          role: 'system',
          type: 'tool_approval_request',
          tool_call_id: 'call_1',
          tool_name: 'run_python',
          arguments: { code: 'print(1)' },
          status: 'pending',
        }}
      />
    )

    // HTML `hidden` (space-y attribute selector) and the utility class.
    expect(container.firstChild).toHaveAttribute('hidden')
    expect(container.firstChild).toHaveClass('hidden')
    expect(container.textContent).toBe('')
  })
})
