import { describe, it, expect } from 'vitest'
import { followAfterScroll, NEAR_BOTTOM_PX } from '../utils/scrollDecision'

const viewport = (scrollTop, scrollHeight = 1000, clientHeight = 400) => ({
  scrollTop, scrollHeight, clientHeight,
})

describe('follow-bottom scroll decisions', () => {
  it('stops following on even a small upward movement near the bottom', () => {
    expect(followAfterScroll(true, 600, viewport(596))).toBe(false)
  })

  it('stays detached on downward movement outside the bottom tolerance', () => {
    expect(followAfterScroll(false, 400, viewport(500))).toBe(false)
  })

  it('resumes after a downward movement into the bottom tolerance', () => {
    expect(followAfterScroll(false, 500, viewport(600 - NEAR_BOTTOM_PX))).toBe(true)
  })

  it('does not reattach merely because an unchanged position is near the bottom', () => {
    expect(followAfterScroll(false, 596, viewport(596))).toBe(false)
  })

  it('ignores subpixel rounding movements', () => {
    expect(followAfterScroll(true, 600, viewport(599.6))).toBe(true)
  })
})
