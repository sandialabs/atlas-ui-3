import { describe, it, expect } from 'vitest'
import { SAVE_MODES, normalizeSaveMode } from '../utils/saveModeConfig'

describe('normalizeSaveMode', () => {
  it.each(SAVE_MODES)('keeps the valid mode %s', (mode) => {
    expect(normalizeSaveMode(mode)).toBe(mode)
  })

  it.each([undefined, null, '', 'incognito', 'SERVER', 1])(
    'falls back to not-saved for an unusable stored value: %s',
    (mode) => {
      expect(normalizeSaveMode(mode)).toBe('none')
    }
  )
})
