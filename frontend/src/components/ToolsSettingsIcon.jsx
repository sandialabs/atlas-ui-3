import { Settings, Wrench } from 'lucide-react'

/**
 * Icon for the combined Tools and Settings panel: a gear with a wrench badge.
 *
 * A bare wrench is the tool picker's glyph (ToolSelector, the Active Tools
 * strip, model capability icons), so on the header it read as "tools only"
 * and hid the general settings, prompts and admin tabs behind it. The gear
 * says "settings"; the badge says tools live there too.
 *
 * `badgeBg` should match the surface behind the icon, hover state included
 * (via `group-hover:`), so the badge reads as a notch cut out of the gear
 * rather than a dark blob.
 */
const ToolsSettingsIcon = ({ size = 'md', className = '', badgeBg = 'bg-gray-800' }) => {
  const gear = size === 'sm' ? 'w-3.5 h-3.5' : 'w-5 h-5'
  const badge = size === 'sm' ? 'w-2 h-2 -right-1 -bottom-1' : 'w-3 h-3 -right-1 -bottom-1'
  return (
    <span aria-hidden="true" className={`relative inline-flex flex-shrink-0 ${className}`}>
      <Settings className={gear} />
      <span className={`absolute ${badge} rounded-full ${badgeBg} flex items-center justify-center`}>
        <Wrench className="w-full h-full p-px" />
      </span>
    </span>
  )
}

export default ToolsSettingsIcon
