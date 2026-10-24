// Formatting for the context meter (issue #877).

export function formatTokens(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '?'
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (value >= 1000) return `${Math.round(value / 1000)}K`
  return String(Math.max(0, Math.round(value)))
}

export const WINDOW_SOURCE_LABEL = {
  config: 'configured for this model',
  litellm: 'from the model catalog',
  fallback: 'assumed: this model\'s window is unknown',
}

export function meterLevel(usage) {
  if (!usage) return 'ok'
  const { estimated_tokens: used, threshold_tokens: threshold, usable_tokens: usable } = usage
  if (typeof used !== 'number') return 'ok'
  if (typeof usable === 'number' && used >= usable) return 'over'
  if (typeof threshold === 'number' && used >= threshold) return 'high'
  if (typeof threshold === 'number' && used >= threshold * 0.75) return 'warn'
  return 'ok'
}
