/**
 * The shared compliance-access rule (utils/complianceAccess).
 *
 * Every surface the header compliance filter touches -- tool/prompt/persona
 * pickers, the data-source panel, the model picker, the selection cleanup and
 * the outgoing payload -- goes through these helpers, so they pin the rule
 * once: the selected level must be one of the resource's allowed data
 * classifications (issue #1032), strict on undeclared resources, and
 * `allowed_with` never widens access.
 */

import { describe, it, expect } from 'vitest'
import {
  isComplianceAccessible,
  classificationsOf,
  complianceLevelsReady,
  keysExcludedByCompliance,
  isModelComplianceAccessible,
  firstCompliantModel,
} from '../utils/complianceAccess'

const LEVELS = [
  { name: 'Public', aliases: [], allowed_with: ['Public'] },
  { name: 'SOC2', aliases: ['SOC 2', 'SOC-2'], allowed_with: ['SOC2'] },
  { name: 'HIPAA', aliases: ['HIPAA-Compliant'], allowed_with: ['HIPAA', 'SOC2'] },
  { name: 'FedRAMP', aliases: [], allowed_with: ['FedRAMP', 'SOC2'] },
]

describe('isComplianceAccessible', () => {
  it('allows everything when no level is selected', () => {
    expect(isComplianceAccessible(LEVELS, null, 'Public')).toBe(true)
    expect(isComplianceAccessible(LEVELS, null, undefined)).toBe(true)
  })

  it('reads a legacy single level as a one-element list; allowed_with does not widen it', () => {
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'HIPAA')).toBe(true)
    // HIPAA lists SOC2 in allowed_with, but a SOC2-only resource is not
    // approved for HIPAA data.
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'SOC2')).toBe(false)
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'Public')).toBe(false)
    expect(isComplianceAccessible(LEVELS, 'SOC2', 'HIPAA')).toBe(false)
  })

  it('admits a level the resource explicitly lists, and only those', () => {
    const multi = ['Public', 'HIPAA', 'FedRAMP']
    expect(isComplianceAccessible(LEVELS, 'Public', multi)).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'HIPAA', multi)).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'FedRAMP', multi)).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'SOC2', multi)).toBe(false)
    // A Public-only resource (an external search tool) never sees HIPAA data.
    expect(isComplianceAccessible(LEVELS, 'HIPAA', ['Public'])).toBe(false)
  })

  it('denies an explicitly empty list under an active filter', () => {
    expect(isComplianceAccessible(LEVELS, 'Public', [])).toBe(false)
    expect(isComplianceAccessible(LEVELS, null, [])).toBe(true)
  })

  it('denies untagged resources under an active filter', () => {
    expect(isComplianceAccessible(LEVELS, 'HIPAA', null)).toBe(false)
    expect(isComplianceAccessible(LEVELS, 'HIPAA', undefined)).toBe(false)
  })

  it('denies everything for a level it does not know', () => {
    expect(isComplianceAccessible(LEVELS, 'Bogus', 'Public')).toBe(false)
    expect(isComplianceAccessible([], 'HIPAA', 'HIPAA')).toBe(false)
  })

  it('resolves aliases on both sides', () => {
    expect(isComplianceAccessible(LEVELS, 'SOC2', 'SOC 2')).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'HIPAA-Compliant', ['SOC2', 'HIPAA'])).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'SOC-2', ['HIPAA-Compliant'])).toBe(false)
  })
})

describe('classificationsOf', () => {
  it('prefers the explicit list over the legacy level', () => {
    expect(classificationsOf({ compliance_level: 'SOC2', allowed_data_classifications: ['HIPAA'] }))
      .toEqual(['HIPAA'])
    expect(classificationsOf({ complianceLevel: 'SOC2', allowedDataClassifications: ['Public'] }))
      .toEqual(['Public'])
  })

  it('falls back to the legacy level, then to undeclared', () => {
    expect(classificationsOf({ compliance_level: 'SOC2' })).toEqual(['SOC2'])
    // An explicit null is the server's effective answer: undeclared. A RAG
    // corpus's own complianceLevel (often CUI) must not stand in for it.
    expect(classificationsOf({ complianceLevel: 'CUI', allowedDataClassifications: null })).toBeNull()
    expect(classificationsOf({ compliance_level: 'Public', allowed_data_classifications: null })).toBeNull()
    expect(classificationsOf({})).toBeNull()
    expect(classificationsOf(null)).toBeNull()
  })

  it('keeps an explicit empty list (approved for nothing)', () => {
    expect(classificationsOf({ compliance_level: 'SOC2', allowed_data_classifications: [] })).toEqual([])
  })
})

describe('complianceLevelsReady', () => {
  it('is ready with no filter, and only once the selected level is defined', () => {
    expect(complianceLevelsReady([], null)).toBe(true)
    expect(complianceLevelsReady([], 'HIPAA')).toBe(false)
    expect(complianceLevelsReady(LEVELS, 'HIPAA')).toBe(true)
    expect(complianceLevelsReady(LEVELS, 'Bogus')).toBe(false)
  })
})

describe('keysExcludedByCompliance', () => {
  const levelOf = {
    soc2_tool: 'SOC2',
    hipaa_tool: 'HIPAA',
    multi_tool: ['SOC2', 'HIPAA'],
    public_tool: 'Public',
    untagged_tool: null,
  }
  const lookup = key => levelOf[key]

  it('returns the keys the filter hides, untagged included', () => {
    const keys = ['soc2_tool', 'hipaa_tool', 'multi_tool', 'public_tool', 'untagged_tool']
    expect(keysExcludedByCompliance(keys, LEVELS, 'HIPAA', lookup))
      .toEqual(['soc2_tool', 'public_tool', 'untagged_tool'])
  })

  it('leaves keys it cannot place alone', () => {
    expect(keysExcludedByCompliance(['unknown_tool'], LEVELS, 'HIPAA', lookup)).toEqual([])
  })

  it('excludes nothing without a filter or before the levels load', () => {
    const keys = new Set(['public_tool', 'untagged_tool'])
    expect(keysExcludedByCompliance(keys, LEVELS, null, lookup)).toEqual([])
    // Levels not loaded yet: every check would deny, so nothing may be pruned.
    expect(keysExcludedByCompliance(keys, [], 'HIPAA', lookup)).toEqual([])
  })
})

describe('model helpers', () => {
  const MODELS = [
    { name: 'public-model', compliance_level: 'Public' },
    { name: 'byok-hipaa', compliance_level: 'HIPAA', api_key_source: 'user', user_has_key: false },
    { name: 'soc2-model', compliance_level: 'SOC2' },
    { name: 'multi-model', compliance_level: 'Public', allowed_data_classifications: ['SOC2', 'FedRAMP'] },
    { name: 'hipaa-model', compliance_level: 'HIPAA' },
    'plain-string-model',
  ]

  it('flags a model outside the filter', () => {
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'public-model')).toBe(false)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'soc2-model')).toBe(false)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'hipaa-model')).toBe(true)
    // The explicit list wins over the legacy level.
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'FedRAMP', 'multi-model')).toBe(true)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'Public', 'multi-model')).toBe(false)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'plain-string-model')).toBe(false)
  })

  it('does not flag a model it cannot place, or before levels load', () => {
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'gone-model')).toBe(true)
    expect(isModelComplianceAccessible(MODELS, [], 'HIPAA', 'public-model')).toBe(true)
  })

  it('picks the first approved model and skips models needing a missing user key', () => {
    expect(firstCompliantModel(MODELS, LEVELS, 'HIPAA')).toBe('hipaa-model')
    expect(firstCompliantModel(MODELS, LEVELS, 'FedRAMP')).toBe('multi-model')
    expect(firstCompliantModel(MODELS, LEVELS, 'SOC2')).toBe('soc2-model')
    expect(firstCompliantModel(MODELS, LEVELS, 'Public')).toBe('public-model')
  })

  it('returns null when nothing qualifies', () => {
    expect(firstCompliantModel(MODELS, [...LEVELS, { name: 'Internal', allowed_with: ['Internal'] }], 'Internal')).toBeNull()
  })
})

describe('HTTP RAG corpus payload (issue #1035)', () => {
  // Discovery now sends complianceLevel: null when the backend omits it, and
  // the effective list (legacy level folded in by the server) separately.
  it('reads the effective list, never the badge', () => {
    const legacy = { id: 'a', complianceLevel: 'HIPAA', allowedDataClassifications: ['HIPAA'] }
    const missing = { id: 'b', complianceLevel: null, allowedDataClassifications: ['SOC2', 'HIPAA'] }
    const narrowedAway = { id: 'c', complianceLevel: 'SECRET', allowedDataClassifications: [] }
    expect(classificationsOf(legacy)).toEqual(['HIPAA'])
    expect(classificationsOf(missing)).toEqual(['SOC2', 'HIPAA'])
    expect(classificationsOf(narrowedAway)).toEqual([])
    expect(isComplianceAccessible(LEVELS, 'SOC2', classificationsOf(legacy))).toBe(false)
    expect(isComplianceAccessible(LEVELS, 'SOC2', classificationsOf(missing))).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'SOC2', classificationsOf(narrowedAway))).toBe(false)
  })
})
