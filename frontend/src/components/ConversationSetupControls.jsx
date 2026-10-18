import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown, CircleHelp, Lock, Plus, Shield } from 'lucide-react'
import { SAVE_MODE_CHOICES, saveModeChoice } from '../utils/saveModePresentation'

/**
 * Conversation setup: security level, save mode, and New Chat in one place.
 *
 * These three controls answer the same question -- what is this conversation,
 * who may see it, and where does it live -- so they are grouped together with
 * the security level first. The save control is a three-choice menu, never a
 * cycling toggle: "Not saved", "Saved in this browser", and "Saved to server"
 * are distinct states, and each one is named in full.
 *
 * The explanatory copy is progressive disclosure (a help button plus
 * descriptions inside the save menu) so first-time users get context without
 * adding permanent noise. `quiet` hides that copy and the help button for
 * people who already know the product; core labels, selected values, and the
 * locked-state guidance always stay visible.
 */

function SaveModeIcon({ choice, className = 'w-4 h-4' }) {
  const { Icon, strikethrough } = choice
  return (
    <span className={`relative inline-flex items-center justify-center ${className}`} aria-hidden="true">
      <Icon className={className} />
      {strikethrough && (
        <span className="absolute inset-0 flex items-center justify-center">
          <span className="block w-[130%] h-0.5 bg-current rotate-45 rounded" />
        </span>
      )}
    </span>
  )
}

/**
 * Security level control. "Security level" is a visible label rather than a
 * bare shield icon, and the selected value uses the select's own bold text.
 * When the conversation has started, a lock icon, "Locked for this chat", and
 * the next step ("Start a new chat...") are all rendered inline -- never left
 * to a hover title.
 */
export function SecurityLevelControl({
  complianceOptions,
  complianceRequired,
  value,
  onChange,
  lock,
  variant = 'bar',
}) {
  const selectId = useId()
  const guidanceId = `${selectId}-guidance`
  const isMenu = variant === 'menu'

  const select = (
    <select
      id={selectId}
      value={lock.locked ? (lock.level || '') : (value || '')}
      onChange={(event) => onChange(event.target.value || null)}
      disabled={lock.locked}
      aria-label="Security level"
      aria-describedby={lock.locked ? guidanceId : undefined}
      data-locked={lock.locked ? 'true' : 'false'}
      className={`w-full bg-gray-600 border border-gray-500 rounded px-2 py-0.5 text-gray-100 text-sm font-semibold focus:outline-none focus:ring-2 focus:ring-blue-500 ${
        isMenu ? 'mt-2' : 'mt-0.5'
      } ${lock.locked ? 'opacity-80 cursor-not-allowed' : ''}`}
    >
      {lock.locked
        ? (lock.level ? null : <option value="">{lock.placeholder}</option>)
        : complianceRequired
          ? !value && <option value="" disabled>Select a level</option>
          : <option value="">All Levels</option>}
      {complianceOptions.map(level => (
        <option key={level.name} value={level.name} title={level.description || undefined}>
          {level.name}
        </option>
      ))}
    </select>
  )

  return (
    <div
      className={
        isMenu
          ? 'px-3 py-2 bg-gray-700 rounded-lg'
          : `w-40 rounded-lg border px-2 py-1 ${
              lock.locked
                ? 'bg-amber-950/30 border-amber-600/60'
                : 'bg-gray-700/40 border-gray-600'
            }`
      }
      data-testid="security-level-control"
      data-locked={lock.locked ? 'true' : 'false'}
    >
      <div className="flex items-center gap-2">
        <Shield className="w-4 h-4 text-blue-400 flex-shrink-0" aria-hidden="true" />
        <label
          htmlFor={selectId}
          className="text-[10px] leading-tight font-semibold uppercase tracking-wide text-gray-300 whitespace-nowrap"
        >
          Security level
        </label>
      </div>
      {select}
      {lock.locked && (
        <p
          id={guidanceId}
          className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-amber-300"
        >
          <Lock className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
          <span className="font-semibold">Locked for this chat</span>
          <span className="text-amber-200/90">Start a new chat to change security level.</span>
        </p>
      )}
    </div>
  )
}

/**
 * Three-state save control.
 *
 * Bar variant: a labelled button that opens a menu of radio items. Menu
 * variant (the compact header menu): the three radio choices are laid out
 * directly, so nothing has to be cycled through.
 */
export function SaveChatControl({ saveMode, onChange, quiet = false, variant = 'bar' }) {
  const current = saveModeChoice(saveMode)
  const [open, setOpen] = useState(false)
  const buttonRef = useRef(null)
  const menuRef = useRef(null)
  const itemRefs = useRef([])
  const menuId = useId()

  // Focus the selected choice when the menu opens, and close on Escape.
  useEffect(() => {
    if (!open) return undefined
    const selectedIndex = SAVE_MODE_CHOICES.findIndex(choice => choice.value === current.value)
    const items = itemRefs.current.filter(Boolean)
    ;(items[selectedIndex] || items[0])?.focus()

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, current.value])

  const moveFocus = (delta) => {
    const items = itemRefs.current.filter(Boolean)
    const index = items.indexOf(document.activeElement)
    const next = items[(index + delta + items.length) % items.length]
    next?.focus()
  }

  const choose = (value) => {
    onChange(value)
    setOpen(false)
    buttonRef.current?.focus()
  }

  if (variant === 'menu') {
    return (
      <div className="px-3 py-2 bg-gray-700 rounded-lg" data-testid="save-chat-control">
        <div className="flex items-center gap-2">
          <SaveModeIcon choice={current} className="w-4 h-4" />
          <span className="text-sm font-medium text-gray-100">Save this chat</span>
        </div>
        {!quiet && (
          <p className="mt-1 text-xs text-gray-400">
            Controls how this conversation is stored. Conversations already in your history stay
            visible.
          </p>
        )}
        <div role="radiogroup" aria-label="Save this chat" className="mt-2 space-y-1">
          {SAVE_MODE_CHOICES.map(choice => {
            const checked = choice.value === current.value
            return (
              <label
                key={choice.value}
                className={`flex items-start gap-3 px-2 py-1.5 rounded-md cursor-pointer transition-colors ${
                  checked ? 'bg-gray-600 text-gray-50' : 'text-gray-200 hover:bg-gray-600/70'
                }`}
              >
                <input
                  type="radio"
                  name="conversation-save-mode"
                  value={choice.value}
                  checked={checked}
                  onChange={() => onChange(choice.value)}
                  className="mt-1 h-4 w-4 accent-blue-500"
                />
                <SaveModeIcon choice={choice} className="w-4 h-4 mt-0.5" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium">{choice.label}</span>
                  {!quiet && (
                    <span className="block text-xs text-gray-400">{choice.description}</span>
                  )}
                </span>
              </label>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="relative" data-testid="save-chat-control">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setOpen(previous => !previous)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Save this chat: ${current.label}`}
        title={quiet ? undefined : 'Choose how this conversation is stored'}
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border border-gray-600 bg-gray-700/40 hover:bg-gray-700 transition-colors text-gray-200"
      >
        <SaveModeIcon choice={current} className="w-4 h-4" />
        <span className="flex flex-col items-start leading-tight">
          <span className="text-[10px] font-medium uppercase tracking-wide text-gray-400 whitespace-nowrap">
            Save this chat
          </span>
          <span className="text-sm font-semibold text-gray-100 whitespace-nowrap">
            {current.label}
          </span>
        </span>
        <ChevronDown className={`w-3.5 h-3.5 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
      </button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label="Save this chat"
            className="absolute left-0 top-full mt-1 w-80 max-w-[calc(100vw-2rem)] bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 overflow-hidden"
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                moveFocus(1)
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                moveFocus(-1)
              } else if (event.key === 'Home') {
                event.preventDefault()
                itemRefs.current.filter(Boolean)[0]?.focus()
              } else if (event.key === 'End') {
                event.preventDefault()
                const items = itemRefs.current.filter(Boolean)
                items[items.length - 1]?.focus()
              } else if (event.key === 'Tab') {
                // Let focus continue naturally, but do not leave a popup menu
                // stranded open behind it.
                setOpen(false)
              }
            }}
          >
            {!quiet && (
              <div className="px-3 pt-3 pb-2 border-b border-gray-700">
                <p className="text-sm font-semibold text-gray-100">How is this chat saved?</p>
                <p className="mt-0.5 text-xs text-gray-400">
                  Controls how this conversation is stored. Conversations already in your history
                  stay visible.
                </p>
              </div>
            )}
            <div className="p-1">
              {SAVE_MODE_CHOICES.map((choice, index) => {
                const checked = choice.value === current.value
                return (
                  <button
                    key={choice.value}
                    ref={(element) => { itemRefs.current[index] = element }}
                    type="button"
                    role="menuitemradio"
                    aria-checked={checked}
                    onClick={() => choose(choice.value)}
                    className={`w-full flex items-start gap-3 px-3 py-2 rounded-md text-left transition-colors ${
                      checked ? 'bg-gray-700 text-gray-50' : 'text-gray-200 hover:bg-gray-700'
                    }`}
                  >
                    <SaveModeIcon choice={choice} className="w-4 h-4 mt-0.5" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">{choice.label}</span>
                      {!quiet && (
                        <span className="block text-xs text-gray-400">{choice.description}</span>
                      )}
                    </span>
                    {checked && <Check className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" aria-hidden="true" />}
                  </button>
                )
              })}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export function NewChatButton({ onClick, showLabel = true, variant = 'bar', quiet = false }) {
  if (variant === 'menu') {
    return (
      <button
        type="button"
        onClick={onClick}
        className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors text-gray-200"
      >
        <Plus className="w-5 h-5" aria-hidden="true" />
        <span>New Chat</span>
        {!quiet && <span className="ml-auto text-xs opacity-60">Ctrl+Alt+N</span>}
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="New Chat"
      title={quiet ? undefined : 'Start New Chat (Ctrl+Alt+N)'}
      className="flex items-center gap-2 px-2 sm:px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-200"
    >
      <Plus className="w-4 h-4 sm:w-5 sm:h-5" aria-hidden="true" />
      {showLabel && <span className="text-sm font-medium whitespace-nowrap">New Chat</span>}
    </button>
  )
}

/**
 * Opt-in help. A single question-mark button reveals a short explanation of
 * the cluster instead of leaving permanent explanatory paragraphs in the
 * header. Hidden entirely in quiet mode.
 */
export function SetupHelp({ quiet = false }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open])

  if (quiet) return null

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(previous => !previous)}
        aria-label="About conversation setup"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title="About security level and saving"
        className="p-1.5 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
      >
        <CircleHelp className="w-4 h-4" aria-hidden="true" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            id={panelId}
            role="dialog"
            aria-label="About conversation setup"
            className="absolute left-0 top-full mt-1 w-80 max-w-[calc(100vw-2rem)] bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 p-3 text-sm"
          >
            <p className="font-semibold text-gray-100">Conversation setup</p>
            <div className="mt-2 space-y-2 text-xs text-gray-300">
              <p>
                <span className="font-semibold text-gray-200">Security level</span> sets what
                information this chat may use. It locks after the conversation starts, so start a
                new chat to change it.
              </p>
              <p>
                <span className="font-semibold text-gray-200">Save this chat</span> controls whether
                this conversation is stored: not saved, in this browser, or on the server. It does
                not hide conversations that are already in your history.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * The cluster itself. `variant="menu"` is the compact header menu layout;
 * `variant="bar"` is the desktop header row.
 */
export default function ConversationSetupCluster({
  complianceOptions = [],
  complianceRequired = false,
  securityValue,
  onSecurityChange,
  lock,
  saveMode,
  onSaveModeChange,
  onNewChat,
  showNewChatLabel = true,
  showSaveMode = true,
  quiet = false,
  variant = 'bar',
}) {
  const hasSecurity = complianceOptions.length > 0

  if (variant === 'menu') {
    return (
      <div className="space-y-2" data-testid="conversation-setup-cluster">
        <h3 className="px-1 text-xs font-semibold uppercase tracking-wide text-gray-400">
          Conversation setup
        </h3>
        {hasSecurity && (
          <SecurityLevelControl
            variant="menu"
            complianceOptions={complianceOptions}
            complianceRequired={complianceRequired}
            value={securityValue}
            onChange={onSecurityChange}
            lock={lock}
          />
        )}
        {showSaveMode && (
          <SaveChatControl
            variant="menu"
            saveMode={saveMode}
            onChange={onSaveModeChange}
            quiet={quiet}
          />
        )}
        <NewChatButton variant="menu" onClick={onNewChat} quiet={quiet} />
        {!quiet && (
          <p className="px-1 text-xs text-gray-500">
            Security level locks when the chat starts. Saving applies to this conversation only.
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2" data-testid="conversation-setup-cluster">
      {hasSecurity && (
        <SecurityLevelControl
          complianceOptions={complianceOptions}
          complianceRequired={complianceRequired}
          value={securityValue}
          onChange={onSecurityChange}
          lock={lock}
        />
      )}
      {showSaveMode && (
        <SaveChatControl
          saveMode={saveMode}
          onChange={onSaveModeChange}
          quiet={quiet}
        />
      )}
      <NewChatButton onClick={onNewChat} showLabel={showNewChatLabel} quiet={quiet} />
      <SetupHelp quiet={quiet} />
    </div>
  )
}
