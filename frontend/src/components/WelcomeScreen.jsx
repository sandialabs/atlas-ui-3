import { useChat } from '../contexts/ChatContext'

const WelcomeScreen = () => {
  const { appName } = useChat()
  const showPoweredByAtlas =
    import.meta.env.VITE_FEATURE_POWERED_BY_ATLAS === 'true'

  return (
    <div className="flex flex-col items-center justify-center flex-1 p-8 text-center relative">
      <div className="mb-8">
        <img
          src="/logo.png"
          alt={`${appName} Logo`}
          className="w-128 h-128 mx-auto mb-4 object-contain"
          onError={(e) => {
            // Fallback to letter avatar if logo fails to load
            e.target.style.display = 'none'
            e.target.nextElementSibling.style.display = 'flex'
          }}
        />
        {/* <div className="w-24 h-24 bg-blue-600 rounded-full items-center justify-center mb-4 mx-auto hidden">
          <span className="text-3xl font-bold text-white">
            {appName.charAt(0)}
          </span>
        </div> */}
      </div>
      
      {/* <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-100 mb-2">{appName}</h1>
      </div> */}
      
      <div className="max-w-md">
        <p className="text-gray-400 text-lg">
          Select a model and start chatting. You can also explore available tools in the side panel.
        </p>
      </div>

      {/* Smaller logo in the lower right corner, feature-flagged */}
      {showPoweredByAtlas && (
        <div className="absolute inset-x-0 bottom-4 flex justify-end px-4 sm:bottom-6 sm:px-6">
          <img
            src="/sandia-powered-by-atlas.png"
            alt="Powered By SNL ATLAS Logo"
            className="w-32 sm:w-40 md:w-56 lg:w-64 object-contain"
            onError={(e) => {
              e.target.style.display = 'none'
            }}
          />
        </div>
      )}

    </div>
  )
}

export default WelcomeScreen
