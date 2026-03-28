import { useChat } from '../contexts/ChatContext'
import AnimatedLogo from './AnimatedLogo'

const animatedLogoEnabled =
  import.meta.env.VITE_FEATURE_ANIMATED_LOGO === 'true'

const WelcomeScreen = () => {
  const { appName } = useChat()

  return (
    <div className="flex flex-col items-center justify-center flex-1 min-h-0 overflow-hidden p-4 sm:p-8 text-center">
      {animatedLogoEnabled ? (
        <AnimatedLogo appName={appName} />
      ) : (
        <img
          src="/logo.png"
          alt={`${appName} Logo`}
          className="max-w-48 sm:max-w-80 md:max-w-2xl lg:max-w-4xl max-h-full mx-auto object-contain [@media(min-height:500px)]:mb-8"
          onError={(e) => {
            e.target.style.display = 'none'
            e.target.nextElementSibling.style.display = 'flex'
          }}
        />
      )}

      <div className="max-w-md flex-shrink-0 hidden [@media(min-height:500px)]:block">
        <p className="text-gray-400 text-lg">
          Select a model and start chatting. To explore available tools, click the Wrench icon or to view files, click the Folder icon in the top right.
        </p>
      </div>
    </div>
  )
}

export default WelcomeScreen
