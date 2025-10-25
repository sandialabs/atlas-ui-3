import { useChat } from '../contexts/ChatContext'

const WelcomeScreen = () => {
  const { appName } = useChat()

  return (
    <div className="flex flex-col items-center justify-center flex-1 p-8 text-center">
      <div className="mb-8">
        <img 
          src="/logo.png" 
          alt={`${appName} Logo`}
          className="w-32 h-32 mx-auto mb-4 object-contain"
          onError={(e) => {
            // Fallback to letter avatar if logo fails to load
            e.target.style.display = 'none'
            e.target.nextElementSibling.style.display = 'flex'
          }}
        />
        <div className="w-24 h-24 bg-blue-600 rounded-full items-center justify-center mb-4 mx-auto hidden">
          <span className="text-3xl font-bold text-white">
            {appName.charAt(0)}
          </span>
        </div>
      </div>
      
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-100 mb-2">{appName}</h1>
      </div>
      
      <div className="max-w-md">
        <p className="text-gray-400 text-lg">
          Select a model and start chatting. You can also explore available tools in the side panel.
        </p>
      </div>
    </div>
  )
}

export default WelcomeScreen