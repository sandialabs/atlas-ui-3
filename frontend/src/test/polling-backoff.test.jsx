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
  function TestPoller({ fetchFn, normalInterval = 5000, maxBackoffDelay = 30000, enabled = true, backoffBase, pauseWhenHidden, deps = [] }) {
    usePollingWithBackoff(fetchFn, { normalInterval, maxBackoffDelay, enabled, backoffBase, pauseWhenHidden, deps })
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

  it('uses backoffBase as the first failure delay', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    vi.spyOn(console, 'log').mockImplementation(() => {})

    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={3000} backoffBase={3000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    // With jitter fixed at 1.0 the first failure retries after backoffBase,
    // not the hook's 1s default: a fast-interval poller must not be retried
    // faster than its healthy cadence. Generous margins rather than the exact
    // boundary, so a loaded runner advancing real time cannot flake it.
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  describe('pauseWhenHidden', () => {
    let hiddenState
    const setHidden = (value) => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => value })
    }

    beforeEach(() => {
      hiddenState = false
      setHidden(hiddenState)
    })

    afterEach(() => {
      delete document.hidden
    })

    it('does not poll while the document is hidden and polls once when shown again', async () => {
      const fetchFn = vi.fn()
      setHidden(true)
      await act(async () => {
        render(<TestPoller fetchFn={fetchFn} normalInterval={1000} pauseWhenHidden />)
        await vi.advanceTimersByTimeAsync(0)
      })
      // Hidden: not even the initial poll runs, and nothing is scheduled.
      expect(fetchFn).not.toHaveBeenCalled()
      await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
      expect(fetchFn).not.toHaveBeenCalled()

      // Shown again: one poll fires immediately and the cadence resumes.
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(fetchFn).toHaveBeenCalledTimes(2)
    })

    it('stops a scheduled poll mid-interval when the tab is hidden', async () => {
      const fetchFn = vi.fn()
      await act(async () => {
        render(<TestPoller fetchFn={fetchFn} normalInterval={1000} pauseWhenHidden />)
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(1)

      // Hide before the next interval elapses: the pending poll is cancelled,
      // so crossing its original deadline fires nothing.
      setHidden(true)
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
      await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
      expect(fetchFn).toHaveBeenCalledTimes(1)

      // Shown again: the fresh poll on show replaces the cancelled one.
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(2)
    })

    it('showing the tab during backoff waits out the delay instead of polling at once', async () => {
      Math.random = () => 0.0 // negative jitter, floored to backoffBase below
      const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
      setHidden(true)
      await act(async () => {
        render(<TestPoller fetchFn={fetchFn} normalInterval={3000} backoffBase={3000} pauseWhenHidden />)
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).not.toHaveBeenCalled()

      // Show: the first poll fails and queues a 3000ms backoff.
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(1)

      // Hide mid-backoff, then show: no immediate poll may bypass the backoff.
      setHidden(true)
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(1)

      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      expect(fetchFn).toHaveBeenCalledTimes(2)
    })

    it('showing after part of the backoff elapsed waits only the remainder', async () => {
      Math.random = () => 0.5 // no jitter; retry is exactly backoffBase
      const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
      setHidden(true)
      await act(async () => {
        render(<TestPoller fetchFn={fetchFn} normalInterval={3000} backoffBase={3000} pauseWhenHidden />)
        await vi.advanceTimersByTimeAsync(0)
      })
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(fetchFn).toHaveBeenCalledTimes(1) // fails at t0; retry due at t0+3000

      // Hide, let 1000ms of the backoff elapse, then show: only 2000 remain.
      // Margins, not the exact boundary.
      setHidden(true)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(1000)
      })
      setHidden(false)
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
      expect(fetchFn).toHaveBeenCalledTimes(1)
      await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
      expect(fetchFn).toHaveBeenCalledTimes(2)
    })
  })

  it('a new generation is not stalled by an in-flight request from the old one', async () => {
    // Switching the polled subject (a dep change) while the old subject's
    // request is still in the air must start the new subject's poll at once;
    // the old request must not hold the new generation's in-flight guard.
    let calls = 0
    const fetchFn = vi.fn(() => {
      calls += 1
      if (calls === 1) return new Promise(() => {}) // never resolves
      return Promise.resolve()
    })
    const { rerender } = render(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    rerender(<TestPoller fetchFn={fetchFn} deps={['B']} normalInterval={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('a new generation starts with a clean backoff count', async () => {
    // A failure from the subject a generation replaced must not make the new
    // subject's first retry use the old backoff exponent.
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    const { rerender } = render(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} backoffBase={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    rerender(<TestPoller fetchFn={fetchFn} deps={['B']} normalInterval={1000} backoffBase={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    // Immediate poll (count reset), not a wait on the old generation's timer.
    expect(fetchFn).toHaveBeenCalledTimes(2)

    // Its first retry is the base delay (1000), not 2000 from an inherited
    // second-failure exponent. Check at 1500ms, comfortably between the two.
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(fetchFn).toHaveBeenCalledTimes(3)
  })

  it('a late failure from a superseded generation does not affect the new one', async () => {
    let rejectFirst
    const fetchFn = vi.fn()
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectFirst = reject }))
      .mockResolvedValueOnce(undefined)
      .mockRejectedValue(new Error('fail'))
    const { rerender } = render(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} backoffBase={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    rerender(<TestPoller fetchFn={fetchFn} deps={['B']} normalInterval={1000} backoffBase={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(2)

    // The superseded generation's request now fails. It must not bump the new
    // generation's failure count (which would double its next retry).
    await act(async () => {
      rejectFirst(new Error('late'))
      await Promise.resolve()
      await Promise.resolve()
    })

    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(fetchFn).toHaveBeenCalledTimes(3)
    // Guarded: the next poll failed once, so its retry is at +1000 (call 4 by
    // t+2500). Unguarded: the inherited count makes the retry +2000, so still
    // 3 calls here.
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(fetchFn).toHaveBeenCalledTimes(4)
  })

  it('does not start a second fetch while one is in flight', async () => {
    let resolveFirst
    const fetchFn = vi.fn(() => new Promise((resolve) => { resolveFirst = resolve }))
    render(<TestPoller fetchFn={fetchFn} normalInterval={1000} pauseWhenHidden />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    // A visibility-triggered poll while the first is still in flight is
    // dropped by the in-flight guard.
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    await act(async () => { resolveFirst(); await Promise.resolve() })
    expect(fetchFn).toHaveBeenCalledTimes(1)
  })

  it('resuming after an enabled toggle honors the saved backoff', async () => {
    // An inactive/active flip is the same subject: it keeps its accumulated
    // backoff and does not immediate-poll a failing endpoint on every resume.
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    const { rerender } = render(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} backoffBase={1000} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(fetchFn).toHaveBeenCalledTimes(1) // count 1, retry due at +1000

    rerender(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} backoffBase={1000} enabled={false} />)
    rerender(<TestPoller fetchFn={fetchFn} deps={['A']} normalInterval={1000} backoffBase={1000} enabled />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    // Resuming waits out the remaining backoff; no immediate request.
    expect(fetchFn).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(900) })
    expect(fetchFn).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('clamps backoffBase to maxBackoffDelay', async () => {
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={5000} backoffBase={5000} maxBackoffDelay={2000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    // The retry is capped at 2000, not the 5000 base.
    await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
    expect(fetchFn).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('does not retry faster than backoffBase when jitter is negative', async () => {
    // calculateBackoffDelay(1, 3000) with a 0.8 jitter factor is 2400ms; the
    // scheduled retry is floored at backoffBase so a fast poller is never
    // retried sooner than its healthy cadence. The 2600ms check sits between
    // the unfloored 2400ms and the floored 3000ms.
    Math.random = () => 0.0
    const fetchFn = vi.fn().mockRejectedValue(new Error('fail'))
    await act(async () => {
      render(<TestPoller fetchFn={fetchFn} normalInterval={3000} backoffBase={3000} />)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(fetchFn).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(2600) })
    expect(fetchFn).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })
})
