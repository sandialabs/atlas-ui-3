import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { useUserActivity } from '../hooks/useUserActivity'

function ActivityProbe({ idleTimeoutMs }) {
  const isActive = useUserActivity({ idleTimeoutMs })
  return <div data-testid="active">{isActive ? 'active' : 'idle'}</div>
}

describe('useUserActivity', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('starts in the active state', async () => {
    let getByTestId
    await act(async () => {
      ;({ getByTestId } = render(<ActivityProbe idleTimeoutMs={1000} />))
    })
    expect(getByTestId('active').textContent).toBe('active')
  })

  it('flips to idle after the timeout with no activity', async () => {
    let getByTestId
    await act(async () => {
      ;({ getByTestId } = render(<ActivityProbe idleTimeoutMs={1000} />))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(getByTestId('active').textContent).toBe('idle')
  })

  it('resets the idle countdown on mousemove', async () => {
    let getByTestId
    await act(async () => {
      ;({ getByTestId } = render(<ActivityProbe idleTimeoutMs={1000} />))
    })

    // Nearly idle, then activity resets the timer.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900)
    })
    await act(async () => {
      window.dispatchEvent(new Event('mousemove'))
      await vi.advanceTimersByTimeAsync(500)
    })
    // Still active: total elapsed is 1400ms but the timer reset at 900ms.
    expect(getByTestId('active').textContent).toBe('active')

    // After another full timeout with no activity, goes idle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(getByTestId('active').textContent).toBe('idle')
  })

  it('returns to active on the next activity event after going idle', async () => {
    let getByTestId
    await act(async () => {
      ;({ getByTestId } = render(<ActivityProbe idleTimeoutMs={1000} />))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(getByTestId('active').textContent).toBe('idle')

    await act(async () => {
      window.dispatchEvent(new Event('keydown'))
    })
    expect(getByTestId('active').textContent).toBe('active')
  })

  it('removes listeners on unmount', async () => {
    let unmount
    let getByTestId
    await act(async () => {
      ;({ unmount, getByTestId } = render(
        <ActivityProbe idleTimeoutMs={1000} />,
      ))
    })
    expect(getByTestId('active').textContent).toBe('active')
    unmount()

    // Activity after unmount must not throw or warn; without the cleanup
    // the hook would attempt a state update on an unmounted component.
    await act(async () => {
      window.dispatchEvent(new Event('mousemove'))
      await vi.advanceTimersByTimeAsync(2000)
    })
  })
})
