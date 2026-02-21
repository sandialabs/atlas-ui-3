import { useChat } from '../contexts/ChatContext'

const WelcomeScreen = () => {
  const { appName } = useChat()

  return (
    <div className="flex flex-col items-center justify-center flex-1 min-h-0 overflow-hidden p-4 sm:p-8 text-center">
      <div className="mb-4 sm:mb-8 flex-shrink min-h-0">
        <img
          src="/logo.png"
          alt={`${appName} Logo`}
          className="max-w-48 sm:max-w-80 md:max-w-4xl mx-auto mb-4 object-contain"
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
          Select a model and start chatting. To explore available tools, click the Wrench icon or to view files, click the Folder icon in the top right.
        </p>
      </div>
    </div>
  )
}

export default WelcomeScreen
