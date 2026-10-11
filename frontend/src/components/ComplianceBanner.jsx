import { useChat } from '../contexts/ChatContext'
import {
  resolveComplianceBanner,
  bannerBackgroundStyle,
  stripeBandStyle,
  EDGE_STRIPE_BAND_HEIGHT,
} from '../utils/complianceBanner'

// Persistent, full-width compliance classification banner (issue #1045).
//
// A security-context indicator, not an authorization mechanism: it never lets
// a user switch levels, and all classification access rules stay server
// enforced. It renders only when a level defines a banner, so deployments that
// do not configure one are unchanged. In normal flow at the top of the app
// shell, so it stays visible while scrolling without overlaying other UI.
function ComplianceBanner() {
  const {
    complianceEnabled,
    complianceLevels,
    activeComplianceFilter,
    activeConversationId,
    messages,
    activeConversationClassification,
  } = useChat()

  const activeConversation = Boolean(activeConversationId) || (messages?.length || 0) > 0
  const banner = resolveComplianceBanner({
    complianceEnabled,
    levels: complianceLevels,
    selectedLevel: activeComplianceFilter,
    activeConversation,
    conversationClassification: activeConversationClassification,
  })

  if (!banner) return null

  const style = bannerBackgroundStyle(banner)
  const bandStyle = stripeBandStyle(banner)
  const pattern = banner.pattern
  const striped = Boolean(pattern && pattern.type !== 'solid')
  const textStyle = { color: banner.text_color || '#FFFFFF' }
  if (striped && pattern.type !== 'edge_stripes') {
    // Keep the label legible over a fully striped background by haloing it in
    // the banner's base color.
    textStyle.textShadow = `0 0 3px ${banner.background_color}, 0 0 3px ${banner.background_color}`
  }

  return (
    <div
      role="status"
      aria-label={`Compliance classification: ${banner.label}`}
      data-testid="compliance-banner"
      data-level={banner.key}
      data-unknown={banner.unknown ? 'true' : 'false'}
      className="relative w-full shrink-0 flex items-center justify-center px-3 min-h-[1.5rem] sm:min-h-[1.75rem] overflow-hidden select-none"
      style={style}
    >
      {bandStyle && (
        <>
          <span
            aria-hidden="true"
            className="absolute inset-x-0 top-0"
            style={{ ...bandStyle, height: EDGE_STRIPE_BAND_HEIGHT }}
          />
          <span
            aria-hidden="true"
            className="absolute inset-x-0 bottom-0"
            style={{ ...bandStyle, height: EDGE_STRIPE_BAND_HEIGHT }}
          />
        </>
      )}
      <span
        className="relative z-10 max-w-full truncate text-center text-[10px] sm:text-xs font-bold uppercase leading-tight tracking-[0.2em]"
        style={textStyle}
      >
        {banner.label}
      </span>
    </div>
  )
}

export default ComplianceBanner
