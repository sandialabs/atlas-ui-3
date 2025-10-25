import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { 
  ArrowLeft, MessageSquare, Settings, Database, Store, Key, Zap, Code, 
  FileText, AlertTriangle, Bot 
} from 'lucide-react'

const HelpPage = () => {
  const navigate = useNavigate()
  const [helpConfig, setHelpConfig] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const fetchHelpConfig = async () => {
      try {
        const response = await fetch('/api/config')
        if (!response.ok) {
          throw new Error('Failed to fetch help configuration')
        }
        const data = await response.json()
        setHelpConfig(data.help_config || { title: "Help & Documentation", sections: [] })
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchHelpConfig()
  }, [])

  const getIcon = (iconName) => {
    const icons = {
      'zap': Zap,
      'message-square': MessageSquare,
      'database': Database,
      'settings': Settings,
      'store': Store,
      'alert-triangle': AlertTriangle,
      'code': Code,
      'file-text': FileText,
      'bot': Bot
    }
    return icons[iconName] || FileText
  }

  const renderQuickStartSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      <div className="space-y-4 text-gray-300">
        <p>{section.description}</p>
        
        <div className="grid md:grid-cols-2 gap-4">
          {section.cards.map((card, index) => (
            <div key={index} className="bg-gray-700 p-4 rounded-lg">
              <h3 className="font-semibold text-white mb-2">{card.title}</h3>
              <p className="text-sm">
                {card.content}
                {card.hasKeyboard && (
                  <kbd className="bg-gray-600 px-2 py-1 rounded text-xs ml-1">{card.keyboardShortcut}</kbd>
                )}
                {card.hasIcon && (
                  <Settings className="w-4 h-4 inline ml-1" />
                )}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )

  const renderFeatureListSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      
      <div className="space-y-6">
        {section.features.map((feature, index) => (
          <div key={index} className={`border-l-4 border-${feature.borderColor} pl-4`}>
            <div className="flex items-center gap-2 mb-2">
              {React.createElement(getIcon(feature.icon), { className: `w-5 h-5 text-${feature.iconColor}` })}
              <h3 className="text-lg font-semibold">{feature.title}</h3>
            </div>
            <p className="text-gray-300 mb-2">{feature.description}</p>
            <ul className="text-sm text-gray-400 space-y-1 ml-4">
              {feature.bullets.map((bullet, bulletIndex) => (
                <li key={bulletIndex}>• {bullet}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )

  const renderTipCardsSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      
      <div className="space-y-4">
        {section.tips.map((tip, index) => (
          <div key={index} className="bg-gray-700 p-4 rounded-lg">
            <h3 className="font-semibold text-white mb-2">{tip.title}</h3>
            <p className="text-gray-300 text-sm">{tip.content}</p>
            {tip.shortcuts && (
              <div className="text-gray-300 text-sm space-y-1 mt-2">
                {tip.shortcuts.map((shortcut, shortcutIndex) => (
                  <p key={shortcutIndex}>
                    <kbd className="bg-gray-600 px-2 py-1 rounded text-xs">{shortcut.key}</kbd> - {shortcut.description}
                  </p>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  )

  const renderTechnicalSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      
      <div className="space-y-6">
        {section.subsections.map((subsection, index) => (
          <div key={index}>
            {subsection.borderColor ? (
              <div className={`border-l-4 border-${subsection.borderColor} pl-4`}>
                <h3 className="text-lg font-semibold mb-2">{subsection.title}</h3>
                <p className="text-gray-300 mb-3">{subsection.description}</p>
                
                {subsection.content && subsection.content.map((content, contentIndex) => (
                  <div key={contentIndex} className="space-y-3">
                    {content.type === 'subsection' && (
                      <div>
                        <h4 className="font-medium text-white">{content.title}</h4>
                        <ul className="text-sm text-gray-400 space-y-1 ml-4">
                          {content.bullets.map((bullet, bulletIndex) => (
                            <li key={bulletIndex}>
                              • <code className="bg-gray-600 px-1 rounded">{bullet.code}</code> - {bullet.description}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {content.type === 'code-example' && (
                      <div>
                        <h4 className="font-medium text-white">{content.title}</h4>
                        <pre className="bg-gray-900 p-3 rounded text-xs text-gray-300 overflow-x-auto">
                          <code>{content.code}</code>
                        </pre>
                      </div>
                    )}
                  </div>
                ))}

                {subsection.bullets && (
                  <ul className="text-sm text-gray-400 space-y-1 ml-4">
                    {subsection.bullets.map((bullet, bulletIndex) => (
                      <li key={bulletIndex}>• {bullet}</li>
                    ))}
                  </ul>
                )}
              </div>
            ) : subsection.type === 'resource-card' ? (
              <div className="bg-gray-700 p-4 rounded-lg">
                <h3 className="font-semibold text-white mb-2">{subsection.title}</h3>
                <div className="text-sm text-gray-300 space-y-1">
                  {subsection.resources.map((resource, resourceIndex) => (
                    <p key={resourceIndex}>
                      • <code className="bg-gray-600 px-1 rounded">{resource.file}</code> - {resource.description}
                    </p>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  )

  const renderSimpleSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {section.iconColor === 'gradient' ? (
          <div className={`w-6 h-6 bg-gradient-to-br from-${section.gradientFrom} to-${section.gradientTo} rounded-full flex items-center justify-center`}>
            <span className="text-xs font-bold">AI</span>
          </div>
        ) : (
          React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })
        )}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      
      <div className="text-gray-300 space-y-3">
        <p>{section.description}</p>
        
        <div className="bg-gray-700 p-4 rounded-lg">
          <h3 className="font-semibold text-white mb-2">Features</h3>
          <ul className="text-sm space-y-1">
            {section.features.map((feature, index) => (
              <li key={index}>• {feature}</li>
            ))}
          </ul>
        </div>
        
        {section.note && (
          <p className="text-sm text-gray-400">
            <strong>Note:</strong> {section.note}
          </p>
        )}
      </div>
    </section>
  )

  const renderSupportCardsSection = (section) => (
    <section key={section.id} className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        {React.createElement(getIcon(section.icon), { className: `w-6 h-6 text-${section.iconColor}` })}
        <h2 className="text-xl font-bold">{section.title}</h2>
      </div>
      
      <div className="text-gray-300 space-y-3">
        <p>{section.description}</p>
        
        <div className="grid md:grid-cols-2 gap-4">
          {section.cards.map((card, index) => (
            <div key={index} className="bg-gray-700 p-4 rounded-lg">
              <h3 className="font-semibold text-white mb-2">{card.title}</h3>
              <p className="text-sm">
                {card.content}
                {card.code && <code className="bg-gray-600 px-1 rounded ml-1">{card.code}</code>}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )

  const renderSection = (section) => {
    switch (section.layout) {
      case 'cards-grid':
        return renderQuickStartSection(section)
      case 'feature-list':
        return renderFeatureListSection(section)
      case 'tip-cards':
        return renderTipCardsSection(section)
      case 'technical':
        return renderTechnicalSection(section)
      case 'simple':
        return renderSimpleSection(section)
      case 'support-cards':
        return renderSupportCardsSection(section)
      default:
        return null
    }
  }

  return (
    <div className="h-screen bg-gray-900 text-gray-200 flex flex-col">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700 p-4 flex-shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Back to Chat"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <h1 className="text-2xl font-bold">{helpConfig?.title || 'Help & Documentation'}</h1>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="w-full p-6 space-y-8">
          {loading && (
            <div className="bg-gray-800 rounded-lg p-6 text-center text-gray-400">
              <div className="animate-pulse">Loading help content...</div>
            </div>
          )}
          
          {error && (
            <div className="bg-red-900/20 border border-red-700 rounded-lg p-6 text-center text-red-400">
              <div className="flex items-center justify-center gap-2">
                <div className="w-5 h-5 bg-red-500 rounded-full flex items-center justify-center text-white text-xs">!</div>
                Error loading help content: {error}
              </div>
            </div>
          )}
          
          {!loading && !error && helpConfig?.sections && (
            <>
              {helpConfig.sections.map(renderSection)}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default HelpPage