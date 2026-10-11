import { Shield } from 'lucide-react'
import { classificationLabel } from '../utils/complianceAccess'

/** A model's data classifications as a badge in a picker row; nothing when it declares none. */
const ClassificationBadge = ({ resource }) => {
  const label = classificationLabel(resource)
  if (!label) return null
  return (
    <span className="inline-flex items-center gap-0.5 px-1 rounded text-[10px] bg-blue-600 text-white">
      <Shield className="w-2.5 h-2.5" />
      {label}
    </span>
  )
}

export default ClassificationBadge
