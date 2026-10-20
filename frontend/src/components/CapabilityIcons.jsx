import { Image as ImageIcon, ImageOff, Wrench } from 'lucide-react'

// Diagonal strike drawn over an icon that has no "off" variant of its own
// (lucide ships ImageOff but no WrenchOff), so both unsupported states share
// the same slashed shape.
const Strike = () => (
  <svg aria-hidden="true" viewBox="0 0 24 24" className="absolute inset-0 w-3.5 h-3.5"
    fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
    <line x1="3" y1="3" x2="21" y2="21" />
  </svg>
)

// One capability icon. Color alone can't tell models apart, so an unsupported
// capability is also slashed, and every icon carries its meaning as a tooltip
// and an accessible name. No extra opacity on the grey: the slash already
// marks the state, and dimming further drops the glyph below 3:1 contrast.
const CapabilityIcon = ({ icon, offIcon, supported, color, yes, no }) => {
  const Icon = supported || !offIcon ? icon : offIcon
  const text = supported ? yes : no
  return (
    <span role="img" aria-label={text} title={text}
      className={`relative inline-flex ${supported ? color : 'text-gray-400'}`}>
      <Icon aria-hidden="true" className="w-3.5 h-3.5" />
      {!supported && !offIcon && <Strike />}
    </span>
  )
}

/** A model row's image-input and tool-calling icons, in both model pickers. */
const CapabilityIcons = ({ vision, tools }) => (
  <>
    <CapabilityIcon icon={ImageIcon} offIcon={ImageOff} supported={!!vision} color="text-green-400"
      yes="Accepts image input" no="No image input" />
    <CapabilityIcon icon={Wrench} supported={tools !== false} color="text-blue-400"
      yes="Supports tool calling" no="No tool calling" />
  </>
)

export default CapabilityIcons
