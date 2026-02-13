import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import { calculateBackoffDelay, usePollingWithBackoff } from '../hooks/usePollingWithBackoff'

const originalRandom = Math.random

describe('calculateBackoffDelay', () => {
  beforeEach(() => {
    // Fix Math.random so jitter factor = 0.8 + 0.5*0.4 = 1.0 (no jitter)
    Math.random = () => 0.5
  })

  afterEach(() => {
    Math.random = originalRandom
  })

  it('returns 0 for zero or negative failures', () => {
    expect(calculateBackoffDelay(0)).toBe(0)
    expect(calculateBackoffDelay(-1)).toBe(0)
  })

  it('doubles delay for each consecutive failure', () => {
    expect(calculateBackoffDelay(1, 1000, 300000)).toBe(1000)
    expect(calculateBackoffDelay(2, 1000, 300000)).toBe(2000)
    expect(calculateBackoffDelay(3, 1000, 300000)).toBe(4000)
    expect(calculateBackoffDelay(4, 1000, 300000)).toBe(8000)
  })

  it('caps at maxDelay', () => {
    expect(calculateBackoffDelay(20, 1000, 300000)).toBe(300000)
  })

  it('uses custom baseDelay', () => {
    expect(calculateBackoffDelay(1, 5000, 300000)).toBe(5000)
    expect(calculateBackoffDelay(2, 5000, 300000)).toBe(10000)
  })

  it('applies jitter when Math.random varies', () => {
    Math.random = () => 0.0 // jitter factor = 0.8
    expect(calculateBackoffDelay(1, 1000, 300000)).toBe(800)

    Math.random = () => 1.0 // jitter factor = 1.2
    expect(calculateBackoffDelay(1, 1000, 300000)).toBe(1200)
  })
})

describe('usePollingWithBackoff', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    Math.random = () => 0.5
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    Math.random = originalRandom
  })

  // Test component that uses the hook
  function TestPoller({ fetchFn, normalInterval = 5000, maxBackoffDelay = 30000, enabled = true }) {
    usePollingWithBackoff(fetchFn, { normalInterval, maxBackoffDelay, enabled })
    return <div>poller</div>
  }

  it('calls fetchFn immediately on mount', async () => {
    const fetchFn = vi.fn()
    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it('polls at normalInterval after success', async () => {
    const fetchFn = vi.fn()
    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={10000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('backs off exponentially on failures', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    vi.spyOn(console, 'log').mockImplementation(() => {})

    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={10000} maxBackoffDelay={30000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    // First backoff: 1s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(2)

    // Second backoff: 2s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(3)

    // Third backoff: 4s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(4)
  })

  it('resets to normalInterval after recovery', async () => {
    let callCount = 0
    const fetchFn = vi.fn().mockImplementation(() => {
      callCount++
      if (callCount <= 2) return Promise.reject(new Error('fail'))
      return Promise.resolve()
    })
    vi.spyOn(console, 'log').mockImplementation(() => {})

    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={10000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1) // fail 1

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000) // 1s backoff
    })
    expect(fetchFn).toHaveBeenCalledTimes(2) // fail 2

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000) // 2s backoff
    })
    expect(fetchFn).toHaveBeenCalledTimes(3) // success

    // Should NOT call before normalInterval (10s)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(3)

    // Should call after normalInterval
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchFn).toHaveBeenCalledTimes(4)
  })

  it('does not poll when enabled is false', async () => {
    const fetchFn = vi.fn()
    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} enabled={false} />)
      await vi.advanceTimersByTimeAsync(30000)
    })
    expect(fetchFn).not.toHaveBeenCalled()
  })
})
