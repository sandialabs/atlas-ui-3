import { useState, useEffect, useCallback, useRef } from 'react'
import { ChevronDown, Wrench, Shield, Key, Eye, Info } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useOptionalMarketplace } from '../contexts/MarketplaceContext'
import { classificationsOf } from '../utils/complianceAccess'
import { useEscapeKey } from '../hooks/useEscapeKey'
import { useLLMAuthStatus } from '../hooks/useLLMAuthStatus'
import TokenInputModal from './TokenInputModal'
import GatewayModelPicker from './GatewayModelPicker'

/**
 * Chat model picker.
 *
 * Moved out of the top bar and under the message box (issue #839 review):
 * the chat bar is where the work happens, so the model in use belongs there
 * rather than in a header the user is not looking at. The menu opens upward
 * because it now sits at the bottom of the viewport.
 */
const ModelSelector = () => {
  const {
    models = [],
    llmGateways = [],
    user,
    currentModel,
    setCurrentModel,
    features,
    complianceLevelFilter,
  } = useChat()
  const marketplace = useOptionalMarketplace()
  const isComplianceAccessible = marketplace?.isComplianceAccessible
  const llmAuth = useLLMAuthStatus()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [expandedModelInfo, setExpandedModelInfo] = useState(null)
  const [llmAuthModalModel, setLlmAuthModalModel] = useState(null)
  const triggerRef = useRef(null)

  // Escape closes the menu and returns focus to its button (PR #839 review).
  const closeAndRestoreFocus = useCallback(() => {
    setDropdownOpen(false)
    setExpandedModelInfo(null)
    triggerRef.current?.focus()
  }, [])
  useEscapeKey(dropdownOpen && !llmAuthModalModel, closeAndRestoreFocus)

  useEffect(() => {
    llmAuth.fetchAuthStatus()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleModelSelect = (model) => {
    setCurrentModel(model)
    setDropdownOpen(false)
    setExpandedModelInfo(null)
  }

  const complianceEnabled = features?.compliance_levels && !!isComplianceAccessible
  const currentEntry = models.find(m => (typeof m === 'string' ? m : m.name) === currentModel)
  const currentObj = typeof currentEntry === 'string' ? { name: currentEntry } : currentEntry
  // A gateway model key is long and opaque; show the model and its team.
  const currentLabel = currentObj?.display_name || currentModel
  // Gateway models are chosen in their gateway's section, not the flat list.
  const listedModels = models.filter(m => typeof m === 'string' || !m.gateway)
  const complianceFiltering = complianceEnabled && !!complianceLevelFilter
  // A gateway stays listed while any of its models passes the filter; the
  // picker then hides the individual models that do not.
  const visibleGateways = complianceFiltering
    ? llmGateways.filter(g => (g.model_classifications || g.compliance_levels || [g.compliance_level])
      .some(classifications => isComplianceAccessible(complianceLevelFilter, classifications)))
    : llmGateways
  const isGatewayModelVisible = complianceFiltering
    ? model => isComplianceAccessible(complianceLevelFilter, classificationsOf(model))
    : null
  const visibleListedModels = complianceFiltering
    ? listedModels.filter(m => isComplianceAccessible(
        complianceLevelFilter, typeof m === 'string' ? undefined : classificationsOf(m)))
    : listedModels
  // The selected model can sit outside the filter (a persisted choice, or no
  // compliant replacement existed when the level changed). The list no longer
  // shows it, so flag it on the button instead of leaving it looking normal.
  const currentOutsideFilter = complianceFiltering && !!currentObj &&
    !isComplianceAccessible(complianceLevelFilter, classificationsOf(currentObj))
  const buttonTitle = currentModel
    ? (currentOutsideFilter
      ? `Model: ${currentLabel} is outside the ${complianceLevelFilter} compliance level; choose a compliant model`
      : `Model: ${currentLabel}`)
    : 'Select a model'

  return (
    <div className="relative">
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setDropdownOpen(!dropdownOpen)}
        className={`flex items-center gap-1 px-2 py-1 rounded transition-colors max-w-[16rem] ${
          currentOutsideFilter
            ? 'bg-amber-900/60 hover:bg-amber-900 text-amber-200 border border-amber-600'
            : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
        }`}
        title={buttonTitle}
        aria-label={currentModel
          ? `Select chat model, currently ${currentLabel}${currentOutsideFilter ? `, outside the ${complianceLevelFilter} compliance level` : ''}`
          : 'Select chat model'}
        aria-expanded={dropdownOpen}
        aria-haspopup="true"
      >
        {currentOutsideFilter && <Shield className="w-3 h-3 flex-shrink-0 text-amber-400" aria-hidden="true" />}
        <span className="text-xs truncate min-w-0">{currentLabel || 'Model...'}</span>
        {currentObj?.api_key_source === 'user' && (
          <Key className={`w-3 h-3 flex-shrink-0 ${
            currentObj.user_has_key || llmAuth.getModelAuth(currentModel)?.authenticated
              ? 'text-green-400'
              : 'text-orange-400'
          }`} />
        )}
        <ChevronDown className={`w-3 h-3 flex-shrink-0 transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
      </button>

      {dropdownOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => { setDropdownOpen(false); setExpandedModelInfo(null) }} />
          <div className="absolute bottom-full left-0 mb-1 w-72 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 max-h-[28rem] overflow-y-auto">
            {listedModels.length === 0 && llmGateways.length === 0 ? (
              <div className="px-4 py-2 text-gray-400 text-sm">No models available</div>
            ) : visibleListedModels.length === 0 && visibleGateways.length === 0 ? (
              <div className="px-4 py-2 text-gray-400 text-sm">
                No models match the {complianceLevelFilter} compliance level
              </div>
            ) : (
              visibleListedModels.map(m => {
                const model = typeof m === 'string' ? { name: m } : m
                const modelName = model.name || m
                const needsUserKey = model.api_key_source === 'user'
                const hasUserKey = model.user_has_key === true || llmAuth.getModelAuth(modelName)?.authenticated === true
                const isDisabled = needsUserKey && !hasUserKey
                const isExpanded = expandedModelInfo === modelName
                return (
                  <div key={modelName} className="border-b border-gray-700 last:border-b-0 first:rounded-t-lg last:rounded-b-lg">
                    <div className="flex items-center">
                      <button
                        onClick={() => !isDisabled && handleModelSelect(modelName)}
                        className={`flex-1 min-w-0 text-left px-3 py-2 text-sm flex items-center gap-2 ${
                          isDisabled ? 'text-gray-500 cursor-not-allowed' : 'text-gray-200 hover:bg-gray-700'
                        }`}
                        disabled={isDisabled}
                        title={isDisabled ? 'Configure your API key to use this model' : modelName}
                      >
                        <span className="truncate">{modelName}</span>
                        <span className="flex items-center gap-1 flex-shrink-0 ml-auto">
                          <Eye className={`w-3.5 h-3.5 ${model.supports_vision ? 'text-green-400' : 'text-gray-600'}`} />
                          <Wrench className={`w-3.5 h-3.5 ${model.supports_tools !== false ? 'text-blue-400' : 'text-gray-600'}`} />
                        </span>
                      </button>
                      {needsUserKey && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            setLlmAuthModalModel(modelName)
                            setDropdownOpen(false)
                          }}
                          className="px-1.5 py-2 hover:bg-gray-700 transition-colors"
                          title={hasUserKey ? 'API key configured (click to change)' : 'Click to add your API key'}
                        >
                          <Key className={`w-3.5 h-3.5 ${hasUserKey ? 'text-green-400' : 'text-orange-400'}`} />
                        </button>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setExpandedModelInfo(isExpanded ? null : modelName)
                        }}
                        className={`px-2 py-2 hover:bg-gray-700 transition-colors ${isExpanded ? 'text-blue-400' : 'text-gray-500 hover:text-gray-300'}`}
                        title="Model info"
                      >
                        <Info className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    {isExpanded && (
                      <div className="px-3 pb-3 pt-2 text-xs bg-gray-900/50 border-t border-gray-700 space-y-2">
                        {model.model_card && (
                          <p className="text-gray-300 leading-relaxed whitespace-pre-line">{model.model_card}</p>
                        )}
                        {!model.model_card && model.description && (
                          <p className="text-gray-400">{model.description}</p>
                        )}
                        <div className="flex flex-wrap gap-1.5">
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium ${model.supports_vision ? 'bg-green-900/50 text-green-400 border border-green-800' : 'bg-gray-800 text-gray-500 border border-gray-700'}`}>
                            <Eye className="w-3 h-3" />
                            Vision {model.supports_vision ? '' : '(no)'}
                          </span>
                          <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium ${model.supports_tools !== false ? 'bg-blue-900/50 text-blue-400 border border-blue-800' : 'bg-gray-800 text-gray-500 border border-gray-700'}`}>
                            <Wrench className="w-3 h-3" />
                            Tools {model.supports_tools !== false ? '' : '(no)'}
                          </span>
                          {complianceEnabled && model.compliance_level && (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-medium bg-blue-600 text-white">
                              <Shield className="w-3 h-3" />
                              {model.compliance_level}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })
            )}
            {visibleGateways.map(gateway => (
              <GatewayModelPicker
                key={gateway.name}
                gateway={gateway}
                currentModel={currentModel}
                onSelect={handleModelSelect}
                user={user}
                isModelVisible={isGatewayModelVisible}
                showCompliance={complianceEnabled}
              />
            ))}
          </div>
        </>
      )}

      <TokenInputModal
        isOpen={llmAuthModalModel !== null}
        serverName={llmAuthModalModel || ''}
        onClose={() => setLlmAuthModalModel(null)}
        onUpload={async (tokenData) => {
          await llmAuth.uploadToken(llmAuthModalModel, tokenData)
          setLlmAuthModalModel(null)
        }}
        isLoading={llmAuth.loading}
        error={llmAuth.error}
      />
    </div>
  )
}

export default ModelSelector
