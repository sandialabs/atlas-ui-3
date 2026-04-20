import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { useUserActivity } from '../hooks/useUserActivity'

function ActivityProbe({ idleTimeoutMs, onChange }) {
  const isActive = useUserActivity({ idleTimeoutMs })
  React.useEffect(() => {
    onChange(isActive)
  }, [isActive, onChange])
  return <div data-testid="active">{isActive ? 'active' : 'idle'}</div>
}

describe('useUserActivity', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('starts in the active state', async () => {
    const onChange = vi.fn()
    await act(async () => {
      render(<ActivityProbe idleTimeoutMs={1000} onChange={onChange} />)
    })
    expect(onChange).toHaveBeenLastCalledWith(true)
  })

  it('flips to idle after the timeout with no activity', async () => {
    const onChange = vi.fn()
    await act(async () => {
      render(<ActivityProbe idleTimeoutMs={1000} onChange={onChange} />)
    })
    onChange.mockClear()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(onChange).toHaveBeenLastCalledWith(false)
  })

  it('resets the idle countdown on mousemove', async () => {
    const onChange = vi.fn()
    await act(async () => {
      render(<ActivityProbe idleTimeoutMs={1000} onChange={onChange} />)
    })
    onChange.mockClear()

    // Nearly idle, then activity resets the timer.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900)
    })
    await act(async () => {
      window.dispatchEvent(new Event('mousemove'))
      await vi.advanceTimersByTimeAsync(500)
    })
    // Still active: total elapsed is 1400ms but the timer reset at 900ms.
    expect(onChange).not.toHaveBeenCalledWith(false)

    // After another full timeout with no activity, goes idle.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(onChange).toHaveBeenLastCalledWith(false)
  })

  it('returns to active on the next activity event after going idle', async () => {
    const onChange = vi.fn()
    await act(async () => {
      render(<ActivityProbe idleTimeoutMs={1000} onChange={onChange} />)
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1001)
    })
    expect(onChange).toHaveBeenLastCalledWith(false)
    onChange.mockClear()

    await act(async () => {
      window.dispatchEvent(new Event('keydown'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(onChange).toHaveBeenLastCalledWith(true)
  })

  it('removes listeners on unmount', async () => {
    const onChange = vi.fn()
    let unmount
    await act(async () => {
      ;({ unmount } = render(
        <ActivityProbe idleTimeoutMs={1000} onChange={onChange} />,
      ))
    })
    unmount()
    onChange.mockClear()

    // Activity after unmount must not trigger state updates.
    await act(async () => {
      window.dispatchEvent(new Event('mousemove'))
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(onChange).not.toHaveBeenCalled()
  })
})
