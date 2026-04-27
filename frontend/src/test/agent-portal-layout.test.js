import { describe, it, expect } from 'vitest'
import {
  DEFAULT_LAYOUT,
  normalizeLayout,
  setLayoutMode,
  placeProcessInLayout,
  clearProcessFromLayout,
  clearSlot,
  moveProcessToSlot,
  countLiveSlots,
} from '../components/agent-portal/layoutHelpers'

describe('agent-portal layoutHelpers', () => {
  it('normalizes empty input to single mode', () => {
    expect(normalizeLayout(null)).toEqual(DEFAULT_LAYOUT)
    expect(normalizeLayout({})).toEqual(DEFAULT_LAYOUT)
    expect(normalizeLayout({ mode: 'bogus' })).toEqual(DEFAULT_LAYOUT)
  })

  it('pads slots to the layout-mode count', () => {
    const out = normalizeLayout({ mode: '2x2', slots: ['a'] })
    expect(out.mode).toBe('2x2')
    expect(out.slots).toEqual(['a', null, null, null])
  })

  it('truncates excess slots on a smaller mode', () => {
    const out = normalizeLayout({ mode: 'single', slots: ['a', 'b', 'c'] })
    expect(out.slots).toEqual(['a'])
  })

  it('coerces non-string slot entries to null', () => {
    const out = normalizeLayout({ mode: '2x2', slots: ['a', 0, false, undefined] })
    expect(out.slots).toEqual(['a', null, null, null])
  })

  it('setLayoutMode preserves prefix slots when growing', () => {
    const out = setLayoutMode({ mode: 'single', slots: ['a'] }, '2x2')
    expect(out).toEqual({ mode: '2x2', slots: ['a', null, null, null] })
  })

  it('setLayoutMode truncates when shrinking', () => {
    const out = setLayoutMode({ mode: '2x2', slots: ['a', 'b', 'c', 'd'] }, 'single')
    expect(out).toEqual({ mode: 'single', slots: ['a'] })
  })

  it('placeProcessInLayout fills the first empty slot', () => {
    const out = placeProcessInLayout({ mode: '2x2', slots: ['a', null, 'c', null] }, 'b')
    expect(out.slots).toEqual(['a', 'b', 'c', null])
  })

  it('placeProcessInLayout honors preferredSlot when empty', () => {
    const out = placeProcessInLayout({ mode: '2x2', slots: [null, null, null, null] }, 'b', 2)
    expect(out.slots).toEqual([null, null, 'b', null])
  })

  it('placeProcessInLayout is a no-op when process already present', () => {
    const layout = { mode: '2x2', slots: ['a', null, null, null] }
    expect(placeProcessInLayout(layout, 'a')).toBe(layout)
  })

  it('clearProcessFromLayout drops every occurrence', () => {
    const out = clearProcessFromLayout({ mode: '2x2', slots: ['a', 'a', 'b', null] }, 'a')
    expect(out.slots).toEqual([null, null, 'b', null])
  })

  it('clearSlot only nulls the named slot', () => {
    const out = clearSlot({ mode: '2x2', slots: ['a', 'b', 'c', 'd'] }, 1)
    expect(out.slots).toEqual(['a', null, 'c', 'd'])
  })

  it('moveProcessToSlot pulls the process from any prior slot', () => {
    const out = moveProcessToSlot({ mode: '2x2', slots: ['a', 'b', null, null] }, 'a', 3)
    expect(out.slots).toEqual([null, 'b', null, 'a'])
  })

  it('moveProcessToSlot is a no-op for invalid target', () => {
    const layout = { mode: '2x2', slots: ['a', null, null, null] }
    expect(moveProcessToSlot(layout, 'a', 99)).toBe(layout)
  })

  it('countLiveSlots ignores nulls', () => {
    expect(countLiveSlots({ mode: '2x2', slots: ['a', null, 'c', null] })).toBe(2)
  })
})
