/**
 * The shared compliance-access rule (utils/complianceAccess).
 *
 * Every surface the header compliance filter touches -- tool/prompt/persona
 * pickers, the data-source panel, the model picker, the selection cleanup and
 * the outgoing payload -- goes through these helpers, so they pin the rule
 * once: explicit allowlist, strict on untagged resources.
 */

import { describe, it, expect } from 'vitest'
import {
  isComplianceAccessible,
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

  it('follows the allowlist, not name equality', () => {
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'HIPAA')).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'SOC2')).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'Public')).toBe(false)
    // not bidirectional
    expect(isComplianceAccessible(LEVELS, 'SOC2', 'HIPAA')).toBe(false)
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
    expect(isComplianceAccessible(LEVELS, 'HIPAA', 'SOC 2')).toBe(true)
    expect(isComplianceAccessible(LEVELS, 'HIPAA-Compliant', 'SOC2')).toBe(true)
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
    public_tool: 'Public',
    untagged_tool: null,
  }
  const lookup = key => levelOf[key]

  it('returns the keys the filter hides, untagged included', () => {
    const keys = ['soc2_tool', 'hipaa_tool', 'public_tool', 'untagged_tool']
    expect(keysExcludedByCompliance(keys, LEVELS, 'HIPAA', lookup))
      .toEqual(['public_tool', 'untagged_tool'])
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
    { name: 'hipaa-model', compliance_level: 'HIPAA' },
    'plain-string-model',
  ]

  it('flags a model outside the filter', () => {
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'public-model')).toBe(false)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'soc2-model')).toBe(true)
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'plain-string-model')).toBe(false)
  })

  it('does not flag a model it cannot place, or before levels load', () => {
    expect(isModelComplianceAccessible(MODELS, LEVELS, 'HIPAA', 'gone-model')).toBe(true)
    expect(isModelComplianceAccessible(MODELS, [], 'HIPAA', 'public-model')).toBe(true)
  })

  it('prefers an exact-level model and skips models needing a missing user key', () => {
    expect(firstCompliantModel(MODELS, LEVELS, 'HIPAA')).toBe('hipaa-model')
    expect(firstCompliantModel(MODELS, LEVELS, 'FedRAMP')).toBe('soc2-model')
    expect(firstCompliantModel(MODELS, LEVELS, 'Public')).toBe('public-model')
  })

  it('returns null when nothing qualifies', () => {
    expect(firstCompliantModel(MODELS, [...LEVELS, { name: 'Internal', allowed_with: ['Internal'] }], 'Internal')).toBeNull()
  })
})
