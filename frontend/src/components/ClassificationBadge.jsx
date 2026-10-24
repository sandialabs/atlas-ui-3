import { Shield } from 'lucide-react'
import { classificationsOf } from '../utils/complianceAccess'
import { complianceDisplayName } from '../utils/complianceDisplay'
import { useOptionalMarketplace } from '../contexts/MarketplaceContext'

/**
 * A model's data classifications on their own line under the model name in a
 * picker row; nothing when it declares none.
 *
 * Kept compact so the name stays the row's primary element (issue #1055): the
 * first classification plus "+N" for the rest. Hovering the row or focusing it
 * with the keyboard (the row carries Tailwind's `group`) swaps in the full,
 * wrapping list, and the full list is always part of the row's accessible name.
 *
 * Each classification renders as its friendly label where the deployment
 * defines one (issue #1117): `display_name ?? name`, aliases resolved. The
 * declared values themselves stay canonical identifiers; without loaded
 * definitions the raw names render, exactly as before.
 */
const ClassificationBadge = ({ resource, levels: levelsProp }) => {
  const marketplace = useOptionalMarketplace()
  const levels = levelsProp ?? marketplace?.complianceLevels
  const list = (classificationsOf(resource) || [])
    .map(name => complianceDisplayName(levels, name))
  if (!list || list.length === 0) return null
  const full = list.join(', ')
  const extra = list.length - 1
  return (
    <span className="flex min-w-0 mt-0.5" data-testid="classification-badge">
      <span
        className="inline-flex items-start gap-0.5 min-w-0 max-w-full px-1 rounded text-[10px] leading-4 bg-blue-600 text-white"
        title={`Allowed data classifications: ${full}`}
      >
        <Shield className="w-2.5 h-2.5 flex-shrink-0 mt-[3px]" aria-hidden="true" />
        <span className="sr-only">Allowed data classifications: {full}</span>
        <span aria-hidden="true" className="flex min-w-0 group-hover:hidden group-focus-visible:hidden">
          <span className="truncate">{list[0]}</span>
          {extra > 0 && <span className="flex-shrink-0 ml-1 font-semibold">+{extra}</span>}
        </span>
        <span aria-hidden="true" className="hidden min-w-0 [overflow-wrap:anywhere] group-hover:inline group-focus-visible:inline">
          {full}
        </span>
      </span>
    </span>
  )
}

export default ClassificationBadge