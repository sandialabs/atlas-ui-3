import { Cloud, Database, HardDrive } from 'lucide-react'

/**
 * Presentation for the three conversation-persistence choices.
 *
 * Kept out of the component file so Fast Refresh can treat
 * ConversationSetupControls as components-only. Order is intentional: least
 * durable to most durable.
 */
export const SAVE_MODE_CHOICES = [
  {
    value: 'none',
    label: 'Not saved',
    Icon: Database,
    strikethrough: true,
    description: 'This chat is not stored after you leave it.',
  },
  {
    value: 'local',
    label: 'Saved in this browser',
    Icon: HardDrive,
    strikethrough: false,
    description: 'Stored on this device only.',
  },
  {
    value: 'server',
    label: 'Saved to server',
    Icon: Cloud,
    strikethrough: false,
    description: 'Stored in your conversation history.',
  },
]

export function saveModeChoice(value) {
  return SAVE_MODE_CHOICES.find(choice => choice.value === value) || SAVE_MODE_CHOICES[0]
}
