// Compliance classification banner resolution (issue #1045).
//
// Presentation only. Which marking the shell shows is decided here so it can
// be unit-tested apart from React: a new/empty conversation follows the
// selected level, an open conversation follows its server-recorded
// classification, and a marking that cannot be trusted never degrades into a
// less restrictive one.

export const NEUTRAL_BANNER = {
  key: '__unavailable__',
  label: 'CLASSIFICATION UNAVAILABLE',
  background_color: '#374151',
  text_color: '#F9FAFB',
  pattern: null,
  unknown: true,
}

// Edge stripes are drawn as narrow bands; bound the band height so the stripes
// cannot overwhelm the compact banner regardless of configured stripe width.
export const EDGE_STRIPE_BAND_HEIGHT = 6

export function findComplianceLevel(levels, name) {
  if (!name) return null
  const list = Array.isArray(levels) ? levels : []
  return list.find(level => level && (
    level.name === name ||
    (Array.isArray(level.aliases) && level.aliases.includes(name))
  )) || null
}

// Whether any defined level opts into a banner. Without one the feature is
// unused and the shell renders nothing, so unconfigured deployments keep their
// existing appearance.
export function anyBannerConfigured(levels) {
  return (Array.isArray(levels) ? levels : []).some(level => level && level.banner)
}

// The banner to render, or null for none.
export function resolveComplianceBanner({
  complianceEnabled,
  levels = [],
  selectedLevel = null,
  activeConversation = false,
  conversationClassification = null,
}) {
  if (!complianceEnabled) return null
  if (!anyBannerConfigured(levels)) return null

  if (!activeConversation) {
    if (!selectedLevel) return null
    const level = findComplianceLevel(levels, selectedLevel)
    return level && level.banner
      ? { ...level.banner, key: level.name, unknown: false }
      : null
  }

  const state = conversationClassification?.state || 'unknown'
  const recorded = conversationClassification?.level || null
  if (state === 'classified' && recorded) {
    const level = findComplianceLevel(levels, recorded)
    // A level the deployment no longer defines cannot be marked with a
    // configured banner; show the neutral indication rather than guessing.
    if (!level) return NEUTRAL_BANNER
    // A defined level without a banner is an opt-out: render nothing for it.
    return level.banner
      ? { ...level.banner, key: level.name, unknown: false }
      : null
  }
  if (state === 'unclassified') return null
  // legacy / invalid / unknown / resolving: never fall back to a less
  // restrictive marking.
  return NEUTRAL_BANNER
}

// Background style for the banner body. Edge stripes are drawn as separate
// top/bottom bands (stripeBandStyle), so the body stays solid under the label.
export function bannerBackgroundStyle(banner) {
  const background = banner?.background_color || '#374151'
  const pattern = banner?.pattern
  if (!pattern || pattern.type === 'solid' || pattern.type === 'edge_stripes') {
    return { backgroundColor: background }
  }
  const width = Number.isFinite(pattern.width) ? pattern.width : 8
  const color = pattern.color
  const angle = Number.isFinite(pattern.angle) ? pattern.angle : 45
  if (pattern.type === 'horizontal_stripes') {
    return {
      backgroundColor: background,
      backgroundImage: stripeGradient(0, color, background, width),
    }
  }
  // diagonal_stripes.
  return {
    backgroundColor: background,
    backgroundImage: stripeGradient(angle, color, background, width),
  }
}

// Bands for edge_stripes, or null for other pattern types.
export function stripeBandStyle(banner) {
  const pattern = banner?.pattern
  if (!pattern || pattern.type !== 'edge_stripes') return null
  const background = banner?.background_color || '#374151'
  const width = Number.isFinite(pattern.width) ? pattern.width : 8
  const angle = Number.isFinite(pattern.angle) ? pattern.angle : 45
  return {
    backgroundImage: stripeGradient(angle, pattern.color, background, width),
  }
}

function stripeGradient(angle, stripe, background, width) {
  return `repeating-linear-gradient(${angle}deg, ${stripe} 0px, ${stripe} ${width}px, ${background} ${width}px, ${background} ${width * 2}px)`
}
