import { X, RotateCcw, LogIn, LogOut, RefreshCw, CheckCircle, AlertCircle, Sparkles, SlidersHorizontal, UserCircle, Wrench, Shield, Sun, Moon, Database } from 'lucide-react'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useTheme } from '../contexts/ThemeContext'
import { useGlobusAuth } from '../hooks/useGlobusAuth'
import PromptManager from './PromptManager'
import ToolsPanel from './ToolsPanel'
import DataSourcesSelector from './DataSourcesSelector'
import AdminQuickPanel from './admin/AdminQuickPanel'
import CaptureConsentSection from './CaptureConsentSection'
import UnsavedChangesDialog from './UnsavedChangesDialog'

// Every tab this panel can show, in display order. Which ones are actually
// visible depends on feature flags and admin membership (see visibleTabs).
const TABS = [
  { id: 'tools', label: 'Tools & Integrations', icon: Wrench },
  { id: 'dataSources', label: 'Data Sources', icon: Database },
  { id: 'prompts', label: 'Prompts', icon: Sparkles },
  { id: 'general', label: 'General', icon: SlidersHorizontal },
  { id: 'userInfo', label: 'User Info', icon: UserCircle },
  { id: 'admin', label: 'Admin', icon: Shield },
]

/**
 * The combined "Tools and Settings" panel (issue #836).
 *
 * One wrench button in the header opens this; tools and integrations, the
 * prompt library, general settings (including the light/dark toggle that used
 * to sit in the top bar), and the most-used admin controls are tabs here
 * instead of separate top-bar entry points.
 */
const SettingsPanel = ({ isOpen, onClose, initialTab = null, promptIntent = null, onPromptIntentConsumed = null }) => {
  // Tools open first when available -- it is what the wrench button reads as.
  // The effect below falls back to the first visible tab when it is not.
  const [activeTab, setActiveTab] = useState('tools')
  const [toolsDirty, setToolsDirty] = useState(false)
  const [promptDirty, setPromptDirty] = useState(false)
  const [showPromptDiscard, setShowPromptDiscard] = useState(false)
  const toolsCloseGuardRef = useRef(null)
  // A tab requested by a caller before the feature flags that make it visible
  // have arrived. Held until it becomes visible, then applied once; cleared as
  // soon as the user picks a tab themselves.
  const pendingTabRef = useRef(null)
  const dialogRef = useRef(null)
  const closeButtonRef = useRef(null)
  // The admin tab is mounted on first visit and kept mounted after that: it
  // fetches on mount (so mounting it eagerly would cost every admin an admin
  // config fetch), but unmounting it on a tab switch throws away the save-result
  // notifications its cards raise.
  const [adminTabVisited, setAdminTabVisited] = useState(false)
  const { theme, toggleTheme } = useTheme()
  // Default settings
  const defaultSettings = {
    llmTemperature: 0.7,
    maxIterations: 10
  }

  // State for settings
  const [settings, setSettings] = useState(defaultSettings)
  const [hasChanges, setHasChanges] = useState(false)

  // Also get live settings from ChatContext for always-in-sync fields
  const { settings: ctxSettings, updateSettings: updateCtxSettings, features, agentModeAvailable, isInAdminGroup } = useChat()
  const customPromptsEnabled = !!features?.custom_prompts
  const toolsEnabled = !!features?.tools
  const ragEnabled = !!features?.rag
  const visibleTabs = useMemo(() => TABS.filter(tab => {
    if (tab.id === 'prompts') return customPromptsEnabled
    if (tab.id === 'tools') return toolsEnabled
    if (tab.id === 'dataSources') return ragEnabled
    if (tab.id === 'admin') return !!isInAdminGroup
    return true
  }), [customPromptsEnabled, toolsEnabled, ragEnabled, isInAdminGroup])

  // An action to run once the panel has actually been dismissed -- "Full Admin
  // Page" navigates, and navigating before the unsaved-selection dialog is
  // answered would drop staged selections without ever showing the prompt.
  const afterCloseRef = useRef(null)

  const finishClose = useCallback(() => {
    const afterClose = afterCloseRef.current
    afterCloseRef.current = null
    onClose()
    afterClose?.()
  }, [onClose])

  // Close attempts route through the tools tab first so unsaved tool
  // selections still raise the confirmation dialog. `afterClose` is deferred
  // until the panel really closes, and dropped if the user cancels out of the
  // dialog. Also used directly as an onClick handler, hence the typeof check.
  const requestClose = useCallback((afterClose = null) => {
    afterCloseRef.current = typeof afterClose === 'function' ? afterClose : null
    // An in-progress prompt draft is asked about first: it is free text, so it
    // is the work most easily lost.
    if (promptDirty) {
      setActiveTab('prompts')
      setShowPromptDiscard(true)
      return
    }
    const guard = toolsCloseGuardRef.current
    if (guard && toolsDirty) {
      setActiveTab('tools')
      guard(() => { afterCloseRef.current = null })
      return
    }
    finishClose()
  }, [finishClose, toolsDirty, promptDirty])

  // "Discard" on the prompt draft falls through to the tools guard, so a close
  // with both kinds of unsaved work still asks about each.
  const discardPromptAndClose = useCallback(() => {
    // Deliberately does NOT clear promptDirty: the draft is only really gone
    // once the panel unmounts, and a later guard can still abort this close. If
    // it does, the next close must ask about the draft again.
    setShowPromptDiscard(false)
    const guard = toolsCloseGuardRef.current
    if (guard && toolsDirty) {
      setActiveTab('tools')
      guard(() => { afterCloseRef.current = null })
      return
    }
    finishClose()
  }, [finishClose, toolsDirty])

  const keepEditingPrompt = useCallback(() => {
    setShowPromptDiscard(false)
    afterCloseRef.current = null
  }, [])

  // Globus auth state
  const {
    authStatus: globusStatus,
    loading: globusLoading,
    error: globusError,
    fetchAuthStatus: fetchGlobusStatus,
    login: globusLogin,
    logout: globusLogout,
    isAuthenticated: globusAuthenticated,
  } = useGlobusAuth()

  // Fetch Globus status when panel opens and feature is enabled
  const fetchGlobusIfEnabled = useCallback(() => {
    if (isOpen && features?.globus_auth) {
      fetchGlobusStatus()
    }
  }, [isOpen, features?.globus_auth, fetchGlobusStatus])

  useEffect(() => {
    fetchGlobusIfEnabled()
  }, [fetchGlobusIfEnabled])

  useEffect(() => {
    if (isOpen && activeTab === 'admin') setAdminTabVisited(true)
  }, [isOpen, activeTab])

  // Forget the visit once the panel closes, so the next open starts fresh.
  useEffect(() => {
    if (!isOpen) setAdminTabVisited(false)
  }, [isOpen])

  // Callers can open the panel straight onto a tab (header wrench, the
  // marketplace return trip, the prompt selector's edit buttons). The request
  // is recorded rather than applied directly: `/api/config` may not have
  // resolved yet, so the requested tab can still be invisible.
  useEffect(() => {
    if (isOpen && initialTab) {
      pendingTabRef.current = initialTab
      setActiveTab(initialTab)
    }
  }, [isOpen, initialTab])

  // Keep the active tab valid as feature flags/admin membership resolve, and
  // honour a still-pending request the moment its tab becomes visible.
  useEffect(() => {
    if (visibleTabs.length === 0) return
    const pending = pendingTabRef.current
    if (pending && visibleTabs.some(tab => tab.id === pending)) {
      pendingTabRef.current = null
      setActiveTab(pending)
      return
    }
    if (!visibleTabs.some(tab => tab.id === activeTab)) {
      setActiveTab(visibleTabs[0].id)
    }
  }, [activeTab, visibleTabs])

  // A tab the user picks themselves supersedes any pending request.
  const selectTab = useCallback((tabId) => {
    pendingTabRef.current = null
    setActiveTab(tabId)
  }, [])

  // Roving tabindex: the tab strip is one stop in the tab order, and Left/Right
  // (plus Home/End) move between tabs, per the ARIA tabs pattern.
  const handleTabKeyDown = useCallback((event) => {
    const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']
    if (!keys.includes(event.key)) return
    event.preventDefault()
    const index = visibleTabs.findIndex(tab => tab.id === activeTab)
    if (index === -1) return
    let next = index
    if (event.key === 'ArrowRight') next = (index + 1) % visibleTabs.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + visibleTabs.length) % visibleTabs.length
    else if (event.key === 'Home') next = 0
    else next = visibleTabs.length - 1
    const target = visibleTabs[next]
    selectTab(target.id)
    document.getElementById(`settings-tab-${target.id}`)?.focus()
  }, [activeTab, visibleTabs, selectTab])

  // Forget a pending request once the panel closes, so reopening it does not
  // jump back to a tab asked for in a previous session of the panel.
  useEffect(() => {
    if (!isOpen) pendingTabRef.current = null
  }, [isOpen])

  // Check for Globus auth callback params in URL
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const globusAuth = params.get('globus_auth')
    const globusError = params.get('globus_error')
    if (globusAuth || globusError) {
      // Clean up URL params
      const url = new URL(window.location)
      url.searchParams.delete('globus_auth')
      url.searchParams.delete('globus_error')
      window.history.replaceState({}, '', url)

      // Refresh status after auth callback
      if (globusAuth === 'success') {
        fetchGlobusStatus()
      }
    }
  }, [fetchGlobusStatus])

  // Load settings from localStorage on mount
  useEffect(() => {
    const savedSettings = localStorage.getItem('chatui-settings')
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings)
        setSettings({ ...defaultSettings, ...parsed })
      } catch (error) {
        console.error('Failed to parse saved settings:', error)
        setSettings(defaultSettings)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Save settings to localStorage whenever they change
  const saveSettings = (newSettings) => {
    localStorage.setItem('chatui-settings', JSON.stringify(newSettings))
    setSettings(newSettings)
    setHasChanges(false)
  }

  const handleSettingChange = (key, value) => {
    const newSettings = { ...settings, [key]: value }
    setSettings(newSettings)
    setHasChanges(true)
  }

  const handleSave = () => {
    saveSettings(settings)
  }

  const handleReset = () => {
    setSettings(defaultSettings)
    saveSettings(defaultSettings)
  }

  // Reverting General is deferred behind the close: a later guard can abort the
  // dismissal, and the panel staying open with the user's in-progress General
  // settings silently thrown away is worse than not reverting at all.
  const revertGeneralSettings = () => {
    const savedSettings = localStorage.getItem('chatui-settings')
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings)
        setSettings({ ...defaultSettings, ...parsed })
      } catch {
        setSettings(defaultSettings)
      }
    } else {
      setSettings(defaultSettings)
    }
    setHasChanges(false)
  }

  const handleCancel = () => {
    requestClose(revertGeneralSettings)
  }

  // This panel is now the only route to tools, theme and admin controls, so it
  // carries proper dialog semantics: focus moves in on open, Escape closes
  // (through the same unsaved guard as the X), and Tab is trapped inside.
  //
  // Nested modals (the unsaved-changes prompt, token input, the admin config
  // editor) render inside this one and mark themselves `role="dialog"`. While
  // one is up it owns both Escape and the trap -- closing the whole panel out
  // from under a half-entered token, or letting Tab wander behind a prompt the
  // user has to answer, would destroy their input.
  const innermostDialog = useCallback(() => {
    const node = dialogRef.current
    if (!node) return null
    const nested = node.querySelectorAll('[role="dialog"]')
    return nested.length > 0 ? nested[nested.length - 1] : node
  }, [])

  // Keyed on isOpen alone: requestClose changes whenever the tools tab goes
  // dirty, and re-running this would yank focus to Close mid-edit.
  useEffect(() => {
    if (!isOpen) return
    closeButtonRef.current?.focus()
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return undefined

    const onKeyDown = (event) => {
      const node = dialogRef.current
      if (!node) return
      const scope = innermostDialog()
      // Clicking non-focusable text drops focus to document.body, so the
      // listener has to live on the document; scope by target instead, and
      // treat "focus fell out of the panel entirely" as still ours.
      if (event.target instanceof Node && node.contains(event.target) === false
          && event.target !== document.body && event.target !== document.documentElement) {
        return
      }

      if (event.key === 'Escape') {
        // A nested overlay owns its own Escape (see useEscapeKey, which listens
        // in the capture phase and stops propagation), so reaching here means
        // none did -- this one is ours.
        if (scope !== node) return
        event.stopPropagation()
        requestClose()
        return
      }
      // Tab is trapped inside the innermost open dialog rather than switched
      // off: standing down would let focus walk out of the overlay entirely.
      if (event.key !== 'Tab') return
      const focusable = Array.from(
        scope.querySelectorAll('a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])')
      // getClientRects() rather than offsetParent: offsetParent is null inside a
      // `position: fixed` ancestor, which is exactly how the nested modals render.
      ).filter(el => el.getClientRects().length > 0)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      // Focus is outside the trapped scope (the body, after a click on plain
      // text): pull it back in rather than letting Tab walk the page behind.
      if (!scope.contains(document.activeElement)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
        return
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [isOpen, requestClose, innermostDialog])

  if (!isOpen) return null

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={requestClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Tools and Settings"
        className="bg-gray-800 rounded-lg shadow-xl max-w-5xl w-full h-[85vh] mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-xl font-semibold text-gray-100 flex items-center gap-2">
            <Wrench className="w-5 h-5 text-blue-400" />
            Tools and Settings
          </h2>
          <button
            ref={closeButtonRef}
            onClick={requestClose}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            aria-label="Close tools and settings"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Tab navigation */}
        <div
          role="tablist"
          aria-label="Tools and settings sections"
          onKeyDown={handleTabKeyDown}
          className="flex items-center gap-1 px-4 border-b border-gray-700 flex-shrink-0 overflow-x-auto"
        >
          {visibleTabs.map((tab) => {
            const Icon = tab.icon
            const isActive = activeTab === tab.id
            // Tools, Prompts, and a visited Admin stay mounted; the others only
            // exist while selected, and aria-controls must not dangle.
            const panelMounted = isActive
              || tab.id === 'tools'
              || tab.id === 'prompts'
              || (tab.id === 'admin' && adminTabVisited)
            return (
              <button
                key={tab.id}
                role="tab"
                id={`settings-tab-${tab.id}`}
                aria-selected={isActive}
                aria-controls={panelMounted ? `settings-tabpanel-${tab.id}` : undefined}
                tabIndex={isActive ? 0 : -1}
                onClick={() => selectTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  isActive
                    ? 'border-blue-500 text-gray-50'
                    : 'border-transparent text-gray-400 hover:text-gray-200'
                }`}
              >
                <Icon className="w-4 h-4" />
                {tab.label}
              </button>
            )
          })}
        </div>

        <UnsavedChangesDialog
          isOpen={showPromptDiscard}
          title="Unsaved Prompt"
          message="You have an unfinished prompt in the editor. Closing now discards it."
          onDiscard={discardPromptAndClose}
          onCancel={keepEditingPrompt}
        />

        {/* Tools & Integrations tab. Kept mounted for the life of the panel so
            pending tool selections survive switching tabs. */}
        {toolsEnabled && (
          <ToolsPanel
            embedded
            isOpen={isOpen}
            active={activeTab === 'tools'}
            onClose={finishClose}
            onNavigate={requestClose}
            closeGuardRef={toolsCloseGuardRef}
            onDirtyChange={setToolsDirty}
          />
        )}

        {/* Data source changes apply immediately, matching the Sources drawer. */}
        {ragEnabled && activeTab === 'dataSources' && (
          <div
            role="tabpanel"
            id="settings-tabpanel-dataSources"
            aria-labelledby="settings-tab-dataSources"
            className="flex-1 overflow-y-auto custom-scrollbar min-h-0"
          >
            <DataSourcesSelector />
          </div>
        )}

        {/* Admin quick controls tab (issue #836). "Full Admin Page" navigates
            away, so it goes through the same unsaved-selection guard as the X. */}
        {isInAdminGroup && adminTabVisited && (
          <div
            role="tabpanel"
            id="settings-tabpanel-admin"
            aria-labelledby="settings-tab-admin"
            hidden={activeTab !== 'admin'}
            className={`${activeTab === 'admin' ? 'flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6' : ''}`}
          >
            <AdminQuickPanel isOpen={isOpen} onNavigate={requestClose} />
          </div>
        )}

        {/* Prompts tab (issue #153). Kept mounted for the life of the panel,
            like the tools tab, so a half-written prompt survives a tab switch. */}
        {customPromptsEnabled && (
          <div
            role="tabpanel"
            id="settings-tabpanel-prompts"
            aria-labelledby="settings-tab-prompts"
            hidden={activeTab !== 'prompts'}
            className={`${activeTab === 'prompts' ? 'flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6' : ''}`}
          >
            <PromptManager
              intent={promptIntent}
              onIntentConsumed={onPromptIntentConsumed}
              onDirtyChange={setPromptDirty}
            />
          </div>
        )}

        {/* User Info tab (placeholder for issue #595) */}
        {activeTab === 'userInfo' && (
          <div
            role="tabpanel"
            id="settings-tabpanel-userInfo"
            aria-labelledby="settings-tab-userInfo"
            className="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6"
          >
            <div className="p-6 bg-gray-700 rounded-lg text-center">
              <UserCircle className="w-10 h-10 text-gray-500 mx-auto mb-3" />
              <h3 className="text-gray-200 font-medium">User Info — Coming Soon</h3>
              <p className="text-sm text-gray-400 mt-2 max-w-md mx-auto">
                A persistent profile (admin defaults plus AI-injected knowledge) that
                can be supplied to RAG and tool calls. Tracked in issue #595.
              </p>
            </div>
          </div>
        )}

        {/* General settings tab */}
        {activeTab === 'general' && (
        <div
          role="tabpanel"
          id="settings-tabpanel-general"
          aria-labelledby="settings-tab-general"
          className="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6 space-y-6"
        >
          {/* Appearance -- moved off the top bar in issue #836 */}
          <div className="bg-gray-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-gray-50 font-medium">Appearance</label>
              <button
                onClick={toggleTheme}
                className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-100 transition-colors text-sm font-medium"
                title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
                {theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              </button>
            </div>
            <p className="text-sm text-gray-400">
              Currently using {theme === 'dark' ? 'dark' : 'light'} mode.
            </p>
          </div>

          {/* LLM Temperature Setting */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-gray-50 font-medium">LLM Temperature</label>
              <span className="text-sm text-gray-400 bg-gray-700 px-2 py-1 rounded">
                {settings.llmTemperature}
              </span>
            </div>
            <div className="space-y-2">
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={settings.llmTemperature}
                onChange={(e) => handleSettingChange('llmTemperature', parseFloat(e.target.value))}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
              />
              <div className="flex justify-between text-xs text-gray-400">
                <span>0 (Deterministic)</span>
                <span>0.5 (Balanced)</span>
                <span>1 (Creative)</span>
              </div>
              <p className="text-sm text-gray-400">
                Controls randomness in AI responses. Lower values are more focused and deterministic, 
                higher values are more creative and varied.
              </p>
            </div>
          </div>

          {agentModeAvailable && (
            <>
              {/* Max Iterations Setting */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-gray-50 font-medium">Max Agent Iterations</label>
                  <span className="text-sm text-gray-400 bg-gray-700 px-2 py-1 rounded">
                    {settings.maxIterations}
                  </span>
                </div>
                <div className="space-y-2">
                  <input
                    type="range"
                    min="1"
                    max="50"
                    step="1"
                    value={settings.maxIterations}
                    onChange={(e) => handleSettingChange('maxIterations', parseInt(e.target.value))}
                    className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
                  />
                  <div className="flex justify-between text-xs text-gray-400">
                    <span>1</span>
                    <span>25</span>
                    <span>50</span>
                  </div>
                  <p className="text-sm text-gray-400">
                    Maximum number of iterations an agent can perform when solving complex tasks. 
                    Higher values allow for more thorough problem solving but may take longer.
                  </p>
                </div>
              </div>
            </>
          )}

          {/* Auto-Approve Tools Setting (always in sync via context) */}
          <div className="bg-gray-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-gray-50 font-medium">Auto-Approve Tool Calls</label>
              <button
                onClick={() => updateCtxSettings({ autoApproveTools: !ctxSettings?.autoApproveTools })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  ctxSettings?.autoApproveTools ? 'bg-green-600' : 'bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ctxSettings?.autoApproveTools ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            <p className="text-sm text-gray-400">
              When enabled, tools that don't require admin approval will execute automatically without prompting.
              Tools that require admin approval will still prompt for confirmation.
            </p>
            {!ctxSettings?.autoApproveTools && (
              <p className="text-sm approval-warning-text mt-2">
                <strong><span aria-hidden="true">⚠</span> Currently:</strong> You will be prompted to approve all tool calls unless admin has disabled approval for specific tools.
              </p>
            )}
          </div>

          {/* Compact Messages Setting (always in sync via context) */}
          <div className="bg-gray-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-gray-50 font-medium">Compact Tool Messages</label>
              <button
                onClick={() => updateCtxSettings({ compactMessages: !(ctxSettings?.compactMessages !== false) })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  ctxSettings?.compactMessages !== false ? 'bg-green-600' : 'bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ctxSettings?.compactMessages !== false ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            <p className="text-sm text-gray-400">
              When enabled (default), tool calls, approval prompts, and system notices render as dense,
              single-line rows. Turn it off to use the classic layout with avatars, author headers, and
              expanded message bubbles.
            </p>
          </div>

          {/* Debug Mode Setting */}
          <div className="bg-gray-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-gray-50 font-medium">Debug Mode</label>
              <button
                onClick={() => {
                  const newVal = !settings.debugMode
                  handleSettingChange('debugMode', newVal)
                  saveSettings({ ...settings, debugMode: newVal })
                }}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  settings.debugMode ? 'bg-green-600' : 'bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    settings.debugMode ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            <p className="text-sm text-gray-400">
              When enabled, tool call messages will show raw input arguments and output results expanded by default,
              making it easier to debug tool interactions.
            </p>
          </div>

          {/* Globus Authentication Section (only shown when feature is enabled) */}
          {features?.globus_auth && (
            <div className="bg-gray-700 rounded-lg p-4">
              <div className="flex items-center justify-between mb-3">
                <label className="text-gray-50 font-medium">Globus Authentication</label>
                <div className="flex items-center gap-2">
                  {globusAuthenticated ? (
                    <span className="flex items-center gap-1 text-sm text-green-400">
                      <CheckCircle className="w-4 h-4" />
                      Connected
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-sm text-gray-400">
                      <AlertCircle className="w-4 h-4" />
                      Not connected
                    </span>
                  )}
                </div>
              </div>
              <p className="text-sm text-gray-400 mb-3">
                Log in with Globus to automatically authenticate with ALCF inference endpoints
                and other Globus-scoped services. Your tokens are stored securely on the server.
              </p>

              {globusError && (
                <div className="p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm mb-3">
                  {globusError}
                </div>
              )}

              {/* Show resource servers with token status */}
              {globusAuthenticated && globusStatus?.resource_servers?.length > 0 && (
                <div className="space-y-2 mb-3">
                  {globusStatus.resource_servers.map((rs) => (
                    <div
                      key={rs.resource_server}
                      className="flex items-center justify-between p-2 bg-gray-600 rounded text-sm"
                    >
                      <span className="text-gray-300 font-mono text-xs truncate max-w-[200px]" title={rs.resource_server}>
                        {rs.resource_server}
                      </span>
                      <span className={rs.is_expired ? 'text-red-400' : 'text-green-400'}>
                        {rs.is_expired ? 'Expired' : 'Valid'}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex items-center gap-2">
                {!globusAuthenticated ? (
                  <button
                    onClick={globusLogin}
                    className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors text-sm font-medium"
                  >
                    <LogIn className="w-4 h-4" />
                    Log in with Globus
                  </button>
                ) : (
                  <>
                    <button
                      onClick={globusLogout}
                      disabled={globusLoading}
                      className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600/80 hover:bg-red-600 text-white transition-colors text-sm font-medium"
                    >
                      <LogOut className="w-4 h-4" />
                      Disconnect
                    </button>
                    <button
                      onClick={fetchGlobusStatus}
                      disabled={globusLoading}
                      className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors text-sm"
                    >
                      <RefreshCw className={`w-4 h-4 ${globusLoading ? 'animate-spin' : ''}`} />
                      Refresh
                    </button>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Help improve Atlas (fine-tune capture, issue #622) — only when the
              finetune_capture feature flag is enabled. */}
          {features?.finetune_capture && (
            <CaptureConsentSection isOpen={isOpen} />
          )}

        </div>
        )}

        {/* Footer Actions (General settings only) */}
        {activeTab === 'general' && (
        <div className="flex items-center justify-between p-6 border-t border-gray-700 flex-shrink-0">
          <button
            onClick={handleReset}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            <RotateCcw className="w-4 h-4" />
            Reset to Defaults
          </button>

          <div className="flex items-center gap-3">
            <button
              onClick={handleCancel}
              className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges}
              className={`px-4 py-2 rounded-lg transition-colors font-medium ${
                hasChanges
                  ? 'bg-blue-600 hover:bg-blue-700 text-white'
                  : 'bg-gray-600 text-gray-400 cursor-not-allowed'
              }`}
            >
              Save Changes
            </button>
          </div>
        </div>
        )}
      </div>
    </div>
  )
}

export default SettingsPanel
