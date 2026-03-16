import { Wrench, FolderOpen, Save, Bot, LayoutPanelLeft, MessageSquare } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import AnimatedLogo from './AnimatedLogo'

const animatedLogoEnabled =
  import.meta.env.VITE_FEATURE_ANIMATED_LOGO === 'true'

const CAPABILITY_CARDS = [
  {
    icon: MessageSquare,
    title: 'Model Selection',
    description: 'Choose an AI model from the dropdown in the header. Different models have different strengths.',
    featureKey: null,
  },
  {
    icon: Wrench,
    title: 'Tools',
    description: 'Click the wrench icon to enable AI tools. Tools let the assistant search, run code, and more.',
    featureKey: 'tools',
  },
  {
    icon: FolderOpen,
    title: 'Files',
    description: 'Click the folder icon to upload and manage files. Attach files to your messages for analysis.',
    featureKey: 'files_panel',
  },
  {
    icon: LayoutPanelLeft,
    title: 'Canvas',
    description: 'A side panel that displays rich content like charts, code, and structured output from the assistant.',
    featureKey: null,
  },
  {
    icon: Save,
    title: 'Save Mode',
    description: 'Control how chats are stored: incognito (not saved), local browser storage, or server-side history.',
    featureKey: null,
  },
  {
    icon: Bot,
    title: 'Agent Mode',
    description: 'Enable agent mode to let the assistant plan and execute multi-step tasks autonomously.',
    featureKey: null,
    agentOnly: true,
  },
]

const SUGGESTED_PROMPTS = [
  'What can you help me with?',
  'Summarize a document for me',
  'Help me write a Python script',
  'Explain a concept step by step',
]

const WelcomeScreen = ({ onSuggestPrompt }) => {
  const { appName, features, agentModeAvailable } = useChat()

  const visibleCards = CAPABILITY_CARDS.filter(card => {
    if (card.agentOnly && !agentModeAvailable) return false
    if (card.featureKey && features?.[card.featureKey] === false) return false
    return true
  })

  return (
    <div className="flex flex-col items-center flex-1 min-h-0 overflow-y-auto p-4 sm:p-8 text-center">
      <div className={animatedLogoEnabled ? 'mb-4 sm:mb-6 flex-shrink-0' : 'mb-2 sm:mb-4 flex-shrink min-h-0'}>
        {animatedLogoEnabled ? (
          <AnimatedLogo appName={appName} />
        ) : (
          <img
            src="/logo.png"
            alt={`${appName} Logo`}
            className="max-w-36 sm:max-w-56 mx-auto object-contain"
            onError={(e) => {
              e.target.style.display = 'none'
              e.target.nextElementSibling.style.display = 'flex'
            }}
          />
        )}
      </div>

      <p className="text-gray-400 text-base mb-6 max-w-lg">
        Select a model in the header, then start chatting. Here is what {appName} can do:
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 max-w-3xl w-full mb-8" data-testid="capability-cards">
        {visibleCards.map(({ icon: Icon, title, description }) => (
          <div
            key={title}
            className="flex items-start gap-3 bg-gray-800 border border-gray-700 rounded-lg p-3 text-left"
          >
            <Icon className="w-5 h-5 text-blue-400 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <div>
              <p className="text-gray-200 text-sm font-medium">{title}</p>
              <p className="text-gray-400 text-xs mt-0.5 leading-relaxed">{description}</p>
            </div>
          </div>
        ))}
      </div>

      {onSuggestPrompt && (
        <div className="max-w-3xl w-full" data-testid="suggested-prompts">
          <p className="text-gray-500 text-xs uppercase tracking-wide mb-2">Try asking</p>
          <div className="flex flex-wrap justify-center gap-2">
            {SUGGESTED_PROMPTS.map(prompt => (
              <button
                key={prompt}
                onClick={() => onSuggestPrompt(prompt)}
                className="text-sm text-gray-300 bg-gray-800 border border-gray-700 hover:border-blue-500 hover:text-blue-300 rounded-full px-3 py-1.5 transition-colors"
              >
                {prompt}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export default WelcomeScreen
