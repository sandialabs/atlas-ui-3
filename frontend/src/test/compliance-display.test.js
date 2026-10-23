/**
 * Friendly compliance level labels (issue #1117).
 *
 * A level may configure a presentation-only `display_name` next to its
 * canonical `name`. These pin the one resolver every surface shares:
 * display_name ?? name, aliases resolved, raw name kept when the definitions
 * are missing or no longer define the level. Matching stays on canonical
 * names everywhere else -- a display name is never an identifier.
 */
import { describe, it, expect } from 'vitest'
import {
  levelDisplayLabel,
  complianceDisplayName,
  classificationsDisplayLabel,
} from '../utils/complianceDisplay'

const LEVELS = [
  {
    name: 'CUI_no_ECI_no_ITAR_no_PII',
    display_name: 'CUI (Restricted)',
    aliases: ['CUI_RESTRICTED'],
  },
  { name: 'Public', aliases: [] },
]

describe('levelDisplayLabel', () => {
  it('prefers display_name over name', () => {
    expect(levelDisplayLabel(LEVELS[0])).toBe('CUI (Restricted)')
  })

  it('falls back to name when display_name is absent', () => {
    expect(levelDisplayLabel(LEVELS[1])).toBe('Public')
    expect(levelDisplayLabel({ display_name: '  ', name: 'Public' })).toBe('Public')
    expect(levelDisplayLabel({ display_name: null, name: 'Public' })).toBe('Public')
  })

  it('survives junk input', () => {
    expect(levelDisplayLabel(null)).toBeNull()
    expect(levelDisplayLabel('Public')).toBeNull()
    expect(levelDisplayLabel({ display_name: 7 })).toBeNull()
  })
})

describe('complianceDisplayName', () => {
  it('resolves a canonical name to its display_name', () => {
    expect(complianceDisplayName(LEVELS, 'CUI_no_ECI_no_ITAR_no_PII')).toBe('CUI (Restricted)')
  })

  it('resolves an alias to the display_name of its level', () => {
    expect(complianceDisplayName(LEVELS, 'CUI_RESTRICTED')).toBe('CUI (Restricted)')
  })

  it('falls back to the canonical name', () => {
    expect(complianceDisplayName(LEVELS, 'Public')).toBe('Public')
  })

  it('keeps a raw name the definitions do not define', () => {
    expect(complianceDisplayName(LEVELS, 'Retired')).toBe('Retired')
  })

  it('keeps the raw name while definitions are missing or loading', () => {
    expect(complianceDisplayName([], 'Public')).toBe('Public')
    expect(complianceDisplayName(undefined, 'Public')).toBe('Public')
  })

  it('handles empty input', () => {
    expect(complianceDisplayName(LEVELS, null)).toBeNull()
    expect(complianceDisplayName(LEVELS, '')).toBeNull()
  })

  it('never turns a display name into an identifier', () => {
    // A display_name is not a name or alias: looking it up yields itself.
    expect(complianceDisplayName(LEVELS, 'CUI (Restricted)')).toBe('CUI (Restricted)')
  })
})

describe('classificationsDisplayLabel', () => {
  it('resolves each declared classification', () => {
    const resource = { allowed_data_classifications: ['CUI_RESTRICTED', 'Public'] }
    expect(classificationsDisplayLabel(resource, LEVELS)).toBe('CUI (Restricted), Public')
  })

  it('reads a legacy single compliance_level', () => {
    expect(classificationsDisplayLabel({ compliance_level: 'Public' }, LEVELS)).toBe('Public')
  })

  it('is null when the resource declares nothing', () => {
    expect(classificationsDisplayLabel({}, LEVELS)).toBeNull()
    expect(classificationsDisplayLabel(null, LEVELS)).toBeNull()
  })
})