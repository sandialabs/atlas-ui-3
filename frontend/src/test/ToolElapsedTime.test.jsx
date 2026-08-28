/**
 * Tests for ToolElapsedTime -- the live ticker shown next to an active tool
 * call. Covers the generic slow-tool warning and the atlas_agent_sleep
 * progress-clock behavior added in #838.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import ToolElapsedTime from '../components/ToolElapsedTime'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

const tick = (seconds) => {
  act(() => {
    vi.advanceTimersByTime(seconds * 1000)
  })
}

const isoNow = () => new Date().toISOString()

describe('ToolElapsedTime -- generic tool', () => {
  it('shows plain seconds under the slow threshold', () => {
    const { container } = render(<ToolElapsedTime timestamp={isoNow()} />)
    tick(5)
    expect(container.textContent).toBe('5s')
  })

  it('rolls into minutes once 60s have passed', () => {
    const { container } = render(<ToolElapsedTime timestamp={isoNow()} />)
    tick(65)
    // 65s is past the 30s slow threshold, so the warning appends.
    expect(container.textContent).toBe('1m 05s- taking longer than expected')
  })

  it('appends the slow-tool warning once past the threshold', () => {
    const { container } = render(<ToolElapsedTime timestamp={isoNow()} />)
    tick(31)
    expect(container.textContent).toContain('taking longer than expected')
  })

  it('does not warn before the threshold', () => {
    const { container } = render(<ToolElapsedTime timestamp={isoNow()} />)
    tick(29)
    expect(container.textContent).not.toContain('taking longer than expected')
  })
})

describe('ToolElapsedTime -- atlas_agent_sleep (#838)', () => {
  it('shows a progress clock against the requested duration', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 1200 }}
      />
    )
    tick(60)
    // 60s elapsed of 1200s requested -> 01:00 of 20:00
    expect(container.textContent).toBe('01:00 of 20:00')
  })

  it('never shows the generic slow-tool warning during a sleep', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 1200 }}
      />
    )
    // 5m is well past the generic 30s threshold, but a 20m sleep is fine.
    tick(300)
    expect(container.textContent).not.toContain('taking longer than expected')
  })

  it('switches to a completing hint once the requested wait has elapsed', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 5 }}
      />
    )
    tick(6)
    expect(container.textContent).toContain('completing...')
  })

  it('shows the completing hint the instant the requested wait is reached', () => {
    // elapsed == requested should already read as overdue (>=, not >), so the
    // hint appears immediately at the boundary instead of lagging a second.
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 5 }}
      />
    )
    tick(5)
    expect(container.textContent).toContain('completing...')
  })

  it('does not show the completing hint before the requested wait', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 1200 }}
      />
    )
    tick(60)
    expect(container.textContent).not.toContain('completing...')
  })

  it('formats hour-plus waits as HH:MM:SS', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 7200 }}
      />
    )
    tick(60)
    // 60s elapsed of 2h requested -> both render as HH:MM:SS for consistency.
    expect(container.textContent).toBe('00:01:00 of 02:00:00')
  })

  it('accepts a numeric-string seconds argument', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: '1200' }}
      />
    )
    tick(60)
    expect(container.textContent).toBe('01:00 of 20:00')
  })

  it('falls back to the generic timer when seconds is missing', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{}}
      />
    )
    tick(31)
    expect(container.textContent).toContain('taking longer than expected')
  })

  it('falls back to the generic timer when seconds is non-positive', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 0 }}
      />
    )
    tick(31)
    expect(container.textContent).toContain('taking longer than expected')
  })

  it('falls back to the generic timer when seconds is non-numeric', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 'soon' }}
      />
    )
    tick(31)
    expect(container.textContent).toContain('taking longer than expected')
  })

  it('does not treat an unrelated tool name as a sleep even with seconds', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="basic_fns_bash"
        arguments={{ seconds: 1200 }}
      />
    )
    tick(31)
    // A bash call that carries a `seconds` arg should still use the generic
    // timer + threshold, not the sleep progress clock.
    expect(container.textContent).toContain('taking longer than expected')
    expect(container.textContent).not.toContain('of 20:00')
  })
})

describe('ToolElapsedTime -- heartbeat total from progressRaw', () => {
  it('uses progressRaw.total as the clock denominator when available', () => {
    // The backend clamps a 10000s request to 300s; the heartbeat reports
    // total=300, so the clock should show the clamped duration, not 10000.
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 10000 }}
        progressRaw={{ progress: 60, total: 300 }}
      />
    )
    tick(60)
    // 60s elapsed of 300s clamped -> 01:00 of 05:00
    expect(container.textContent).toBe('01:00 of 05:00')
    expect(container.textContent).not.toContain('20:00')
  })

  it('falls back to args.seconds before the first heartbeat arrives', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 1200 }}
        progressRaw={undefined}
      />
    )
    tick(60)
    expect(container.textContent).toBe('01:00 of 20:00')
  })

  it('uses the consolidated tool name (atlas_sleep) too', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_sleep"
        arguments={{ seconds: 1200 }}
        progressRaw={{ progress: 60, total: 1200 }}
      />
    )
    tick(60)
    expect(container.textContent).toBe('01:00 of 20:00')
  })
})

describe('ToolElapsedTime -- stale hint for stuck sleep', () => {
  it('shows "connection may be lost" past the overdue threshold', () => {
    // 900s sleep, 900 + 61 = 961s elapsed -> stale
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 900 }}
      />
    )
    tick(961)
    expect(container.textContent).toContain('connection may be lost')
    expect(container.textContent).not.toContain('completing...')
  })

  it('shows "completing..." before the stale threshold', () => {
    // 900s sleep, 910s elapsed -> overdue but not yet stale (< 900 + 60)
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 900 }}
      />
    )
    tick(910)
    expect(container.textContent).toContain('completing...')
    expect(container.textContent).not.toContain('connection may be lost')
  })

  it('does not show stale hint for a sleep still in progress', () => {
    const { container } = render(
      <ToolElapsedTime
        timestamp={isoNow()}
        toolName="atlas_agent_sleep"
        arguments={{ seconds: 1200 }}
      />
    )
    tick(60)
    expect(container.textContent).not.toContain('connection may be lost')
    expect(container.textContent).not.toContain('completing...')
  })
})