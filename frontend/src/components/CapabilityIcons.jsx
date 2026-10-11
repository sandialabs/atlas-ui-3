import { Eye, Wrench } from 'lucide-react'

// One capability icon. Color alone can't tell models apart, so the icon also
// carries its meaning as a tooltip and an accessible name.
const CapabilityIcon = ({ icon, supported, color, yes, no }) => {
  const Icon = icon
  const text = supported ? yes : no
  return (
    <span role="img" aria-label={text} title={text} className="inline-flex">
      <Icon aria-hidden="true" className={`w-3.5 h-3.5 ${supported ? color : 'text-gray-600'}`} />
    </span>
  )
}

/** A model row's vision and tool icons, in both model pickers. */
const CapabilityIcons = ({ vision, tools }) => (
  <>
    <CapabilityIcon icon={Eye} supported={!!vision} color="text-green-400"
      yes="Accepts images" no="No image input" />
    <CapabilityIcon icon={Wrench} supported={tools !== false} color="text-blue-400"
      yes="Uses tools" no="No tool use" />
  </>
)

export default CapabilityIcons
