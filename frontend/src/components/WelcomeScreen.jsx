import { useChat } from '../contexts/ChatContext'
import AnimatedLogo from './AnimatedLogo'

const animatedLogoEnabled =
  import.meta.env.VITE_FEATURE_ANIMATED_LOGO === 'true'

const WelcomeScreen = () => {
  const { appName } = useChat()

  return (
    <div className="flex flex-col items-center justify-center flex-1 min-h-0 p-4 sm:p-8 text-center">
      <div className="flex-1 min-h-0 flex items-end mb-4 sm:mb-8">
        {animatedLogoEnabled ? (
          <AnimatedLogo appName={appName} />
        ) : (
          <img
            src="/logo.png"
            alt={`${appName} Logo`}
            className="max-w-48 sm:max-w-80 md:max-w-4xl max-h-full mx-auto object-contain"
            onError={(e) => {
              e.target.style.display = 'none'
              e.target.nextElementSibling.style.display = 'flex'
            }}
          />
        )}
      </div>

      <div className="max-w-md flex-shrink-0">
        <p className="text-gray-400 text-lg">
          Select a model and start chatting. To explore available tools, click the Wrench icon or to view files, click the Folder icon in the top right.
        </p>
      </div>
    </div>
  )
}

export default WelcomeScreen
