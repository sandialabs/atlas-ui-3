import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import WorkspaceSelector from './WorkspaceSelector'
import ConversationSetupCluster, { NewChatButton, SecurityLevelControl } from './ConversationSetupControls'
import { AgentModeHelpButton, AgentModeMenuHint } from './AgentModeHelp'
import ToolsSettingsIcon from './ToolsSettingsIcon'
import { Database, Bot, ExternalLink, FileText, CircleHelp, FolderOpen, Monitor, Menu, X, PanelLeft, Printer } from 'lucide-react'
import { classificationLock } from '../utils/conversationClassification'
import { useElementSize } from '../hooks/useElementSize'
import { useToast } from './ui/toastContext'

// The header has three shapes, widest first:
//
//   full     the conversation setup group (security level, save mode, New
//            Chat, help) and every action button (transcript, agent mode,
//            tools and settings, help, files, canvas) in the bar;
//   compact  the setup group stays in the bar, the action buttons move into
//            the overflow menu;
//   minimal  icons only -- New Chat, Sources, Workspace, plus the security
//            level in its compact form -- and everything else in the menu.
//
// Which shape applies is decided by the header's own measured width, never
// by the viewport: the header sits beside a resizable conversations sidebar,
// so a viewport query reveals controls the header has no room for. The old
// fixed threshold did exactly that (issue #1071): with the compliance
// (security level) selector enabled the setup group is ~150px wider, so at
// intermediate widths the trailing buttons were pushed past the right edge
// of the page and became unreachable.

// The bar's horizontal padding, used when the two groups are added up. The
// bar pads by 16px on each side from the `sm` breakpoint up.
const HEADER_HORIZONTAL_PADDING = 32

// The gap between the connection status and the action cluster. The bar's own
// gap is 16px from the `sm` breakpoint up, and the full bar is far wider than
// that, so the larger value is the one that applies.
const RIGHT_GROUP_GAP = 16

// The room the bar keeps for the user name. The name is the one part of the
// action cluster that may shrink (it truncates, and keeps its full address in
// its title), so the fit test reserves this much for it rather than its full
// width: a long address must not push the buttons off the page.
const USER_NAME_MIN_WIDTH = 96

// Header width at which the compact shape still holds the setup group beside
// the menu button. Below it the bar drops to the minimal shape, and the save
// mode and setup help move into the overflow menu. The number is a budget for
// the widest setup group (a long save-mode label, a long workspace name)
// plus the connection status and the menu button.
export const COMPACT_ACTIONS_MIN_WIDTH = 1160

// Below this the security level leaves the bar for the overflow menu, where
// it stays reachable. It is the narrowest bar that still holds the compact
// security level beside the icon-only buttons and, on small screens, the
// conversations toggle.
export const SECURITY_LEVEL_MIN_WIDTH = 420

// Below this the left-hand buttons drop their text labels and show icons only.
// This is the header's own width rather than a Tailwind viewport breakpoint
// (`hidden md:inline`): the header sits beside a sidebar and a canvas panel, so
// a viewport query kept labels at widths where they no longer fit and dropped
// them at widths where they did (issue #839 review -- "New chat has this
// problem in particular").
export const ACTION_LABELS_MIN_WIDTH = 800

// Below this the connection status shows its dot only. The text used to
// follow the viewport (`hidden sm:inline`), so a wide sidebar on a wide
// screen left the bar with a status label it had no room for.
export const STATUS_TEXT_MIN_WIDTH = 560

const Header = ({ ragPanelOpen, onToggleSidebar, onToggleRag, onToggleFiles, onToggleCanvas, onCloseCanvas, onToggleSettings }) => {
  const navigate = useNavigate()
  const {
    user,
    agentModeAvailable,
    agentModeEnabled,
    setAgentModeEnabled,
    saveMode,
    setSaveMode,
    openChatAsText,
    messages,
    clearChat,
    features,
    settings,
    complianceLevelFilter,
    setComplianceLevelFilter,
    complianceRequired,
    activeConversationClassification,
    conversationAccepted,
    selectedDataSources
  } = useChat()
  const { complianceLevels } = useMarketplace()
  // A conversation the server has accepted is bound to its recorded
  // classification (issue #1044): the selector is locked, showing that level
  // rather than the global value, and only a New Chat unlocks it. A first turn
  // the server refused leaves the selector open to correct the level. The server
  // enforces the same boundary (#1043); this keeps the UI from offering a
  // transition it would refuse.
  const lock = classificationLock({
    complianceEnabled: !!features?.compliance_levels,
    activeConversationClassification,
    conversationAccepted,
  })
  const displayLevel = lock.locked ? lock.level : complianceLevelFilter
  // A saved level stays selectable even if the definitions failed to load or
  // no longer list it, so the user can always clear a filter that is hiding
  // everything; otherwise the selector would vanish with the filter active.
  // Gate on the feature flag, not just the option list: a persisted level can
  // synthesize an option after an operator disables compliance levels, and the
  // selector must not expose an inactive setting.
  const complianceOptions = !features?.compliance_levels
    ? []
    : displayLevel && !complianceLevels.some(l => l.name === displayLevel)
      ? [...complianceLevels, { name: displayLevel, description: lock.locked ? 'Recorded classification' : 'Not defined by this deployment' }]
      : complianceLevels
  // Power users can turn off onboarding copy, hover hints, and the explanatory
  // link in the conversation-setup cluster. Core labels, selected values, and
  // the locked-state guidance never depend on this.
  const reduceHelpText = settings?.reduceHelpText === true
  const { connectionStatus, isConnected } = useWS()
  const toast = useToast()
  const [transcriptDropdownOpen, setTranscriptDropdownOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  // The bar is laid out from its own measured width, not the viewport's: the
  // header sits beside a 256px sidebar, so a viewport query would reveal the
  // cluster while the header still lacks room for it. See useElementSize.
  const [headerRef, headerSize] = useElementSize()
  const headerWidth = headerSize.width
  // The parts of the bar that are measured as they render: the left group
  // (the conversation setup group plus the sources and workspace controls),
  // the row of action buttons on the right, and the connection status.
  const [leftGroupRef, leftGroupSize] = useElementSize()
  const [actionClusterRef, actionClusterSize] = useElementSize()
  const [statusRef, statusSize] = useElementSize()
  const leftGroupWidth = leftGroupSize.width
  const actionClusterWidth = actionClusterSize.width
  const statusWidth = statusSize.width

  // The full bar needs both groups side by side. Measuring them is what makes
  // the threshold follow the content instead of a fixed guess: the compliance
  // (security level) selector widens the setup group by ~150px, a long
  // save-mode label or workspace name widens it further, and the set of
  // action buttons depends on the deployment's features. A fixed threshold
  // either pushed the trailing buttons off the right edge of the page (issue
  // #1071) or collapsed the bar while there was still room for it.
  const fullBarMinWidth =
    leftGroupWidth +
    statusWidth +
    RIGHT_GROUP_GAP +
    actionClusterWidth +
    USER_NAME_MIN_WIDTH +
    HEADER_HORIZONTAL_PADDING
  // The full shape is only ever a widening of the compact one: the two share
  // the same left group, so a bar that collapsed because the left group
  // shrank would immediately widen again. The compact threshold is the floor
  // that keeps the minimal shape from claiming a width the setup group needs.
  const showFullActions = headerWidth >= fullBarMinWidth && headerWidth >= COMPACT_ACTIONS_MIN_WIDTH
  const showCompactActions = headerWidth >= COMPACT_ACTIONS_MIN_WIDTH
  // The security level stays in the bar for as long as it fits: in the
  // compact shape it is part of the setup group, and in the minimal shape it
  // stays in its compact form until even that no longer fits.
  const showSecurityInBar = showCompactActions || headerWidth >= SECURITY_LEVEL_MIN_WIDTH
  const showActionLabels = headerWidth >= ACTION_LABELS_MIN_WIDTH
  const showStatusText = headerWidth >= STATUS_TEXT_MIN_WIDTH


  // Reset the compact menu once the header is wide enough for the full
  // cluster, so it does not spring back open if the header narrows again. The
  // render above is already gated on !showFullActions, so this is state
  // hygiene rather than what actually hides the menu.
  useEffect(() => {
    if (showFullActions) setMobileMenuOpen(false)
  }, [showFullActions])

  // Close dropdowns when mobile menu opens
  useEffect(() => {
    if (mobileMenuOpen) {
      setTranscriptDropdownOpen(false)
    }
  }, [mobileMenuOpen])

  // Handle Escape key to close mobile menu
  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape') setMobileMenuOpen(false)
    }
    if (mobileMenuOpen) {
      document.addEventListener('keydown', handleEscape)
      return () => document.removeEventListener('keydown', handleEscape)
    }
  }, [mobileMenuOpen])

  // One New Chat path for the cluster, the compact menu, and the hotkey, so all
  // three do the same cleanup and focus the composer afterwards.
  const startNewChat = useCallback(() => {
    clearChat()
    onCloseCanvas()
    setMobileMenuOpen(false)
    setTimeout(() => {
      const messageInput = document.querySelector('textarea[placeholder*="message"]')
      if (messageInput) {
        messageInput.focus()
      }
    }, 100)
  }, [clearChat, onCloseCanvas])

  // Handle hotkey for new chat (Ctrl+Alt+N)
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.altKey && (event.key === 'N' || event.key === 'n')) {
        event.preventDefault()
        event.stopPropagation()
        startNewChat()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true) // Use capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [startNewChat])

  // Handle hotkey for toggling agent mode (Ctrl+Alt+A). Mirrors the Ctrl+Alt+N
  // new-chat shortcut. Ctrl+Alt avoids the Ctrl+A "select all" collision, and
  // works even while the message input is focused (capture phase + preventDefault).
  useEffect(() => {
    if (!agentModeAvailable) return
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.altKey && (event.key === 'A' || event.key === 'a')) {
        event.preventDefault()
        event.stopPropagation()
        const next = !agentModeEnabled
        setAgentModeEnabled(next)
        toast.info(`Agent Mode ${next ? 'enabled' : 'disabled'}`)
      }
    }
    document.addEventListener('keydown', handleKeyDown, true) // Use capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [agentModeAvailable, agentModeEnabled, setAgentModeEnabled, toast])

  return (
    <header ref={headerRef} className="flex items-center justify-between p-2 sm:p-4 bg-gray-800 border-b border-gray-700">
      {/* Left section. Deliberately not min-w-0: these are fixed-size icon
          buttons with nothing to truncate, so letting the section shrink below
          them only makes it paint over the right-hand group. The right section
          shrinks instead, truncating the model name. */}
      <div ref={leftGroupRef} data-testid="header-left-group" className="flex items-center gap-2 sm:gap-4">
        {/* Mobile sidebar toggle */}
        {features?.chat_history && (
          <button
            onClick={onToggleSidebar}
            className="md:hidden p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-200"
            title="Conversations"
          >
            <PanelLeft className="w-5 h-5" />
          </button>
        )}

        {/* Conversation setup: security level, save mode, then New Chat. The
            compact and full bars carry the whole group; the minimal bar keeps
            only the security level (in its compact form) and New Chat, and
            puts the save mode and the setup help in the overflow menu. */}
        {showCompactActions ? (
          <ConversationSetupCluster
            complianceOptions={complianceOptions}
            complianceRequired={complianceRequired}
            securityValue={complianceLevelFilter}
            onSecurityChange={setComplianceLevelFilter}
            lock={lock}
            saveMode={saveMode}
            onSaveModeChange={setSaveMode}
            onNewChat={startNewChat}
            showNewChatLabel={showActionLabels}
            showSaveMode={!!features?.chat_history}
            hasMessages={messages.length > 0}
            quiet={reduceHelpText}
          />
        ) : (
          <div className="flex items-center gap-2">
            {showSecurityInBar && complianceOptions.length > 0 && (
              <SecurityLevelControl
                density="compact"
                complianceOptions={complianceOptions}
                complianceRequired={complianceRequired}
                value={complianceLevelFilter}
                onChange={setComplianceLevelFilter}
                lock={lock}
              />
            )}
            <NewChatButton onClick={startNewChat} showLabel={showActionLabels} quiet={reduceHelpText} />
          </div>
        )}

        {features?.rag && (
          <button
            onClick={onToggleRag}
            aria-expanded={ragPanelOpen}
            aria-haspopup="dialog"
            aria-controls="rag-drawer"
            aria-label={showActionLabels
              ? undefined
              : `Toggle Data Sources drawer${selectedDataSources?.size ? `, ${selectedDataSources.size} selected` : ''}`}
            className={`flex items-center gap-2 px-2 sm:px-3 py-2 rounded-lg transition-colors ${
              selectedDataSources?.size > 0
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
            }`}
            title="Toggle Data Sources"
          >
            <Database className="w-4 h-4 sm:w-5 sm:h-5" />
            {showActionLabels && (
              // Capped so a large source count cannot widen the bar: the
              // header budgets for this button, and a count is not worth
              // pushing a control off the page.
              <span className="text-sm font-medium whitespace-nowrap max-w-[5rem] truncate">
                {selectedDataSources?.size > 0 ? `${selectedDataSources.size} sources` : 'Sources'}
              </span>
            )}
          </button>
        )}
        
        {/* Workspace switcher: prompt + data sources + tools as one context.
            The name follows the bar's own width like every other label, so a
            narrow bar with a wide sidebar does not squeeze it out. */}
        {features?.workspaces && <WorkspaceSelector showName={showActionLabels} />}
      </div>

      {/* Right section */}
      <div className="flex items-center gap-2 sm:gap-4 min-w-0 relative">
        {/* The model picker moved to the chat bar (issue #839 review): the
            message box is where people work, so the model in use belongs there.
            See ModelSelector, rendered by ChatArea. */}

        {/* Connection Status - the dot is always shown, the text only when the
            bar has room for it. The text is capped: a status can carry a
            whole sentence ("Unauthenticated: ..."), and that must not push
            the buttons off the page. */}
        <div ref={statusRef} data-testid="header-status" className="flex items-center gap-2 text-xs flex-shrink-0">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className={`text-gray-400 max-w-[5rem] truncate ${showStatusText ? 'inline' : 'hidden'}`}>
            {connectionStatus}
          </span>
        </div>

        {/* The action cluster. When the bar is too narrow for it, the cluster
            moves into the overflow menu -- but it stays in the DOM, out of
            flow, so that the bar can still measure how much room it would
            need. That measurement is what decides whether the full bar
            fits, so it has to be available in the collapsed shape too.

            The cluster is two parts: the user name, which is the one piece of
            unbounded content and the one that gives way (it truncates, and
            keeps its full address in the title), and the row of buttons,
            which never shrink. Keeping the buttons in their own
            `w-max` wrapper means their width is the same whether the bar is
            showing them or measuring them, so the fit test below is not
            thrown off by a cluster that has been squeezed. */}
        <div
          data-testid="header-action-cluster"
          className={
            showFullActions
              ? 'flex items-center gap-2 min-w-0'
              : // `invisible` (visibility: hidden) is what takes the collapsed
                // cluster out of the accessibility tree and the tab order;
                // `absolute` takes it out of the flow so it can still be
                // measured without taking up room.
                'absolute invisible pointer-events-none flex items-center gap-2 w-max'
          }
        >
          {/* User Info. The one piece of unbounded content in the bar, so it
              is the one that gives way: it truncates (and keeps its full
              address in the title) when the bar is tight. The buttons around
              it never shrink, so they can never be pushed off the page. */}
          <div className="text-sm text-gray-300 min-w-0 max-w-[12rem] truncate" title={user}>
            {user}
          </div>

          {/* The buttons, measured as one block. */}
          <div ref={actionClusterRef} data-testid="header-action-buttons" className="flex items-center gap-2 w-max flex-shrink-0">
          {/* Open Chat Transcript Button */}
          <div className="relative flex-shrink-0">
            <button
              onClick={() => setTranscriptDropdownOpen(!transcriptDropdownOpen)}
              disabled={messages.length === 0}
              aria-haspopup="menu"
              aria-expanded={transcriptDropdownOpen}
              className={`p-2 rounded-lg transition-colors ${
                messages.length === 0 
                  ? 'bg-gray-700 text-gray-500 cursor-not-allowed' 
                  : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
              }`}
              title="Open Chat Transcript"
            >
              <FileText className="w-5 h-5" />
            </button>
            
            {transcriptDropdownOpen && messages.length > 0 && (
              <div role="menu" className="absolute right-0 top-full mt-1 w-56 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50">
                {/* Plain text is the primary export: it is readable everywhere
                    and needs no extra tooling. Print / Save as PDF is the only
                    other choice offered here. */}
                <button
                  role="menuitem"
                  onClick={() => {
                    openChatAsText()
                    setTranscriptDropdownOpen(false)
                  }}
                  title="Opens a plain-text transcript in a new browser tab"
                  className="w-full text-left px-4 py-2 text-sm font-medium text-blue-300 hover:bg-gray-700 first:rounded-t-lg flex items-center gap-2"
                >
                  <ExternalLink className="w-4 h-4" />
                  Open as plain text
                </button>
                <button
                  role="menuitem"
                  onClick={() => {
                    window.print()
                    setTranscriptDropdownOpen(false)
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 last:rounded-b-lg flex items-center gap-2"
                >
                  <Printer className="w-4 h-4" />
                  Print / Save as PDF
                </button>
              </div>
            )}
          </div>

          {/* Agent Mode Toggle Button. The explanation is progressive
              disclosure behind the adjacent help button, so the toolbar keeps
              its icon-only density for people who already know the feature. */}
          {agentModeAvailable && (
            <div className="flex items-center gap-0.5 flex-shrink-0">
              <button
                onClick={() => setAgentModeEnabled(!agentModeEnabled)}
                className={`flex-shrink-0 p-2 rounded-lg transition-colors ${
                  agentModeEnabled 
                    ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
                title={reduceHelpText
                  ? `Agent Mode: ${agentModeEnabled ? 'ON' : 'OFF'} (Ctrl+Alt+A)`
                  : (agentModeEnabled
                    ? 'Agent Mode: ON -- the assistant can take multiple steps with your selected tools (click or Ctrl+Alt+A to disable)'
                    : 'Agent Mode: OFF -- turn on to let the assistant take multiple steps with your selected tools (click or Ctrl+Alt+A)')}
                aria-label={`Agent Mode: ${agentModeEnabled ? 'on' : 'off'}`}
                aria-pressed={agentModeEnabled}
              >
                <Bot className="w-5 h-5" />
              </button>
              <AgentModeHelpButton quiet={reduceHelpText} toolsEnabled={!!features?.tools} />
            </div>
          )}

          {/* The admin shield is gone from the toolbar (issue #839 review):
              admin controls are the Admin tab of Tools and Settings, and the
              full dashboard is one click further in from there. */}

          {/* Tools and Settings -- tools, integrations, prompts, general
              settings (including light/dark), and admin quick controls all
              live behind this one button (issue #836). */}
          <button
            type="button"
            onClick={() => onToggleSettings()}
            className="group flex-shrink-0 p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Tools and Settings: tools, integrations, prompts, and general settings"
            aria-label="Tools and Settings"
            aria-haspopup="dialog"
          >
            <ToolsSettingsIcon badgeBg="bg-gray-700 group-hover:bg-gray-600" />
          </button>

          {/* Help. Icon only, and a ringed question mark rather than the old
              outline circle: the reviewer asked for a clearer help glyph so the
              word could go and the toolbar could lose a chunk of text. */}
          <button
            onClick={() => navigate('/help')}
            className="flex-shrink-0 p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-blue-300"
            title="Help"
            aria-label="Help"
          >
            <CircleHelp className="w-5 h-5" />
          </button>

          {/* File Manager Panel Toggle */}
          {features?.files_panel && (
            <button
              onClick={onToggleFiles}
              className="flex-shrink-0 p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              title="File Manager"
            >
              <FolderOpen className="w-5 h-5" />
            </button>
          )}
          
          {/* Canvas Panel Toggle */}
          <button
            onClick={onToggleCanvas}
            className="flex-shrink-0 p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Toggle Canvas"
          >
            <Monitor className="w-5 h-5" />
          </button>
          </div>
        </div>

        {/* Hamburger Menu Button - Only visible on mobile/tablet */}
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className={`${showFullActions ? 'hidden' : 'block'} p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors`}
          title="Menu"
          aria-expanded={mobileMenuOpen}
          aria-controls="mobile-menu"
          aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
        >
          {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Mobile Menu Overlay. Gated on the render, not just on the effect below:
          an effect runs after paint, so keying this on state alone would flash
          the backdrop over the desktop cluster for a frame -- and let it swallow
          a click -- while the header is being widened past the threshold. */}
      {mobileMenuOpen && !showFullActions && (
        <>
          {/* Backdrop */}
          <div 
            className="fixed inset-0 bg-black bg-opacity-50 z-40" 
            onClick={() => setMobileMenuOpen(false)}
          />
          
          {/* Menu Panel */}
          {/* The menu hangs off the bottom of the bar. The bar's height
              depends on its shape -- the security level control is taller than
              the icon buttons -- so the offset is measured rather than
              hard-coded, and the panel never covers the bar's own controls. */}
          <div
            id="mobile-menu"
            className="fixed right-0 w-64 bg-gray-800 border-l border-gray-700 shadow-lg z-50 overflow-y-auto"
            style={{
              top: headerSize.height,
              maxHeight: `calc(100vh - ${headerSize.height}px)`,
            }}
          >
            <div className="p-4 space-y-2">
              {/* User Info */}
              <div className="px-3 py-2 text-sm text-gray-300 bg-gray-700 rounded-lg">
                User: {user}
              </div>

              {/* Conversation setup comes first in the compact menu too:
                  security level, save mode, New Chat, in that order. */}
              <ConversationSetupCluster
                variant="menu"
                complianceOptions={complianceOptions}
                complianceRequired={complianceRequired}
                securityValue={complianceLevelFilter}
                onSecurityChange={setComplianceLevelFilter}
                lock={lock}
                saveMode={saveMode}
                onSaveModeChange={setSaveMode}
                onNewChat={startNewChat}
                showSaveMode={!!features?.chat_history}
                hasMessages={messages.length > 0}
                quiet={reduceHelpText}
              />

              {/* Open Chat Transcript. Plain text is primary; Print / Save as
                  PDF follows. JSON export is intentionally not offered here. */}
              <button
                onClick={() => {
                  openChatAsText()
                  setMobileMenuOpen(false)
                }}
                disabled={messages.length === 0}
                title="Opens a plain-text transcript in a new browser tab"
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  messages.length === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-gray-700 hover:bg-gray-600 text-blue-300'
                }`}
              >
                <ExternalLink className="w-5 h-5" />
                <span>Open as plain text</span>
              </button>

              <button
                onClick={() => {
                  window.print()
                  setMobileMenuOpen(false)
                }}
                disabled={messages.length === 0}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  messages.length === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
              >
                <Printer className="w-5 h-5" />
                <span>Print / Save as PDF</span>
              </button>

              {/* Agent Mode Toggle */}
              {agentModeAvailable && (
                <button
                  onClick={() => {
                    setAgentModeEnabled(!agentModeEnabled)
                    setMobileMenuOpen(false)
                  }}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                    agentModeEnabled 
                      ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                      : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                  }`}
                >
                  <Bot className="w-5 h-5" />
                  <span className="flex flex-col items-start text-left">
                    <span>Agent Mode: {agentModeEnabled ? 'ON' : 'OFF'}</span>
                    {!reduceHelpText && <AgentModeMenuHint />}
                  </span>
                  <span className="ml-auto text-xs opacity-60">Ctrl+Alt+A</span>
                </button>
              )}

              {/* Tools and Settings (issue #836) */}
              <button
                onClick={() => {
                  onToggleSettings()
                  setMobileMenuOpen(false)
                }}
                className="group w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <ToolsSettingsIcon badgeBg="bg-gray-700 group-hover:bg-gray-600" />
                <span>Tools and Settings</span>
              </button>

              {/* Help Button */}
              <button
                onClick={() => {
                  navigate('/help')
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <CircleHelp className="w-5 h-5" />
                <span>Help</span>
              </button>

              {/* File Manager Panel Toggle */}
              {features?.files_panel && (
                <button
                  onClick={() => {
                    onToggleFiles()
                    setMobileMenuOpen(false)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
                >
                  <FolderOpen className="w-5 h-5" />
                  <span>File Manager</span>
                </button>
              )}
              
              {/* Canvas Panel Toggle */}
              <button
                onClick={() => {
                  onToggleCanvas()
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <Monitor className="w-5 h-5" />
                <span>Canvas</span>
              </button>
            </div>
          </div>
        </>
      )}

      {/* Close download dropdown when clicking outside */}
      {transcriptDropdownOpen && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setTranscriptDropdownOpen(false)}
        />
      )}

    </header>
  )
}

export default Header
