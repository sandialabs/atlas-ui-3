/**
 * Compliance classification banner resolution (issue #1045).
 *
 * Presentation-only: a new conversation follows the selected level, an open
 * conversation follows its recorded classification, and an untrustworthy
 * marking becomes a neutral unavailable indication -- never a less
 * restrictive level.
 */

import { describe, it, expect } from 'vitest'
import {
  resolveComplianceBanner,
  anyBannerConfigured,
  findComplianceLevel,
  bannerBackgroundStyle,
  stripeBandStyle,
  NEUTRAL_BANNER,
} from '../utils/complianceBanner'

const LEVELS = [
  { name: 'UUR', aliases: [], allowed_with: ['UUR'], banner: { label: 'UUR', background_color: '#007A33', text_color: '#FFFFFF' } },
  {
    name: 'CUI',
    aliases: ['CUI-Basic'],
    allowed_with: ['CUI'],
    banner: {
      label: 'CONTROLLED UNCLASSIFIED INFORMATION',
      background_color: '#502B85',
      text_color: '#FFFFFF',
      pattern: { type: 'edge_stripes', color: '#9871B9', width: 8, angle: 45 },
    },
  },
  { name: 'Internal', aliases: [], allowed_with: ['Internal'] },
]

const base = { complianceEnabled: true, levels: LEVELS }

describe('anyBannerConfigured', () => {
  it('is true only when at least one level opts in', () => {
    expect(anyBannerConfigured(LEVELS)).toBe(true)
    expect(anyBannerConfigured([{ name: 'Internal' }])).toBe(false)
    expect(anyBannerConfigured(undefined)).toBe(false)
  })
})

describe('findComplianceLevel', () => {
  it('resolves a canonical name or an alias', () => {
    expect(findComplianceLevel(LEVELS, 'CUI').name).toBe('CUI')
    expect(findComplianceLevel(LEVELS, 'CUI-Basic').name).toBe('CUI')
    expect(findComplianceLevel(LEVELS, 'nope')).toBeNull()
  })
})

describe('resolveComplianceBanner', () => {
  it('renders nothing when compliance levels are disabled', () => {
    expect(resolveComplianceBanner({ ...base, complianceEnabled: false, selectedLevel: 'CUI' })).toBeNull()
  })

  it('renders nothing when no level configures a banner', () => {
    expect(resolveComplianceBanner({
      complianceEnabled: true,
      levels: [{ name: 'Internal' }],
      selectedLevel: 'Internal',
    })).toBeNull()
  })

  it('follows the selected level for a new/empty conversation', () => {
    const banner = resolveComplianceBanner({ ...base, selectedLevel: 'UUR' })
    expect(banner.label).toBe('UUR')
    expect(banner.unknown).toBe(false)
  })

  it('renders nothing for a new conversation with no level selected', () => {
    expect(resolveComplianceBanner({ ...base, selectedLevel: null })).toBeNull()
  })

  it('renders nothing for a selected level without a banner', () => {
    expect(resolveComplianceBanner({ ...base, selectedLevel: 'Internal' })).toBeNull()
  })

  it('uses the recorded classification for an open conversation over the selector', () => {
    const banner = resolveComplianceBanner({
      ...base,
      selectedLevel: 'UUR',
      activeConversation: true,
      conversationClassification: { state: 'classified', level: 'CUI' },
    })
    expect(banner.label).toBe('CONTROLLED UNCLASSIFIED INFORMATION')
    expect(banner.key).toBe('CUI')
  })

  it('resolves a recorded alias', () => {
    const banner = resolveComplianceBanner({
      ...base,
      activeConversation: true,
      conversationClassification: { state: 'classified', level: 'CUI-Basic' },
    })
    expect(banner.key).toBe('CUI')
  })

  it('shows the neutral marking for a recorded level the deployment no longer defines', () => {
    const banner = resolveComplianceBanner({
      ...base,
      activeConversation: true,
      conversationClassification: { state: 'classified', level: 'Retired' },
    })
    expect(banner).toBe(NEUTRAL_BANNER)
  })

  it('shows the neutral marking for legacy, invalid, unknown or resolving records', () => {
    for (const state of ['legacy', 'invalid', 'unknown', 'resolving']) {
      expect(resolveComplianceBanner({
        ...base,
        activeConversation: true,
        conversationClassification: { state, level: null },
      })).toBe(NEUTRAL_BANNER)
    }
    expect(resolveComplianceBanner({ ...base, activeConversation: true, conversationClassification: null }))
      .toBe(NEUTRAL_BANNER)
  })

  it('renders nothing for an explicitly unclassified conversation', () => {
    expect(resolveComplianceBanner({
      ...base,
      activeConversation: true,
      conversationClassification: { state: 'unclassified', level: null },
    })).toBeNull()
  })

  it('renders nothing for a classified conversation on a level without a banner', () => {
    expect(resolveComplianceBanner({
      ...base,
      activeConversation: true,
      conversationClassification: { state: 'classified', level: 'Internal' },
    })).toBeNull()
  })
})

describe('banner styles', () => {
  const stripeBanner = {
    background_color: '#502B85',
    pattern: { type: 'diagonal_stripes', color: '#9871B9', width: 8, angle: 45 },
  }

  it('keeps the edge-stripe body solid and stripes the bands', () => {
    const edge = {
      background_color: '#502B85',
      pattern: { type: 'edge_stripes', color: '#9871B9', width: 8, angle: 45 },
    }
    expect(bannerBackgroundStyle(edge).backgroundImage).toBeUndefined()
    expect(bannerBackgroundStyle(edge).backgroundColor).toBe('#502B85')
    expect(stripeBandStyle(edge).backgroundImage).toContain('repeating-linear-gradient')
  })

  it('applies the gradient to the body for full-banner stripes', () => {
    expect(bannerBackgroundStyle(stripeBanner).backgroundImage).toContain('repeating-linear-gradient')
    expect(stripeBandStyle(stripeBanner)).toBeNull()
  })

  it('draws a solid body with no band for a solid banner', () => {
    expect(bannerBackgroundStyle({ background_color: '#007A33' })).toEqual({ backgroundColor: '#007A33' })
    expect(stripeBandStyle({ background_color: '#007A33' })).toBeNull()
  })
})
