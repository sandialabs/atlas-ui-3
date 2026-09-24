/**
 * Toasts can carry a single inline action (used by New Chat's Undo).
 * The action has to be a real, finger-sized button -- the whole point of
 * replacing the blocking confirm was to make the recovery path tappable.
 */

import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { ToastProvider } from '../components/ui/ToastProvider'
import { useToast } from '../components/ui/toastContext'

function Trigger({ onUndo }) {
  const toast = useToast()
  return (
    <button onClick={() => toast.info('New chat started.', { duration: 0, action: { label: 'Undo', onClick: onUndo } })}>
      go
    </button>
  )
}

const renderWithToasts = (onUndo) =>
  render(
    <ToastProvider>
      <Trigger onUndo={onUndo} />
    </ToastProvider>
  )

describe('toast actions', () => {
  it('renders the action label and runs it on click', async () => {
    const onUndo = vi.fn()
    renderWithToasts(onUndo)
    act(() => screen.getByText('go').click())

    const action = screen.getByTestId('toast-action')
    expect(action.textContent).toBe('Undo')
    await act(async () => {
      action.click()
    })
    expect(onUndo).toHaveBeenCalledTimes(1)
  })

  it('dismisses the toast after the action runs', async () => {
    renderWithToasts(vi.fn())
    act(() => screen.getByText('go').click())
    // The action handler awaits the callback before dismissing, so the
    // dismiss lands a microtask later -- flush it inside act().
    await act(async () => {
      screen.getByTestId('toast-action').click()
    })
    expect(screen.queryByTestId('toast-action')).toBeNull()
  })

  it('renders no action button when none was supplied', () => {
    render(
      <ToastProvider>
        <PlainTrigger />
      </ToastProvider>
    )
    act(() => screen.getByText('plain').click())
    expect(screen.getByText('hello')).toBeTruthy()
    expect(screen.queryByTestId('toast-action')).toBeNull()
  })
})

function PlainTrigger() {
  const toast = useToast()
  return <button onClick={() => toast.info('hello', { duration: 0 })}>plain</button>
}
