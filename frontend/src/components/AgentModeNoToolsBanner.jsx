import { Sparkles, Wrench } from 'lucide-react'
import { openSettingsPanel } from '../utils/settingsPanelEvents'

/**
 * Composer callout for Agent Mode with nothing for the loop to act on.
 * Extracted from ChatArea so the contrast fixture can mount the real markup;
 * colours come from the generated .atlas-notice-alert tint (index.css).
 */
export default function AgentModeNoToolsBanner({
  toolsEnabled, reduceHelpText, currentModelToolSupport, currentModelLabel, onTurnOff,
}) {
  return (
    <div
      className="mb-2 px-3 py-3 atlas-notice-alert border-2 rounded-lg"
      data-testid="agent-mode-no-tools-banner"
    >
      <div className="flex items-start gap-3">
        <Sparkles className="w-5 h-5 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          {/* Only the title is announced; the explanation and buttons
              are reachable but not re-read on every change. */}
          <p className="text-sm font-semibold" role="status">
            {toolsEnabled
              ? 'Agent Mode is on, but no tools are selected'
              : 'Agent Mode is on, but tools are disabled'}
          </p>
          {/* The title and actions stay put on short viewports; only the
              explanation scrolls, so the composer never gets pushed
              off screen and the tools action stays reachable. */}
          <div
            data-testid="agent-mode-no-tools-detail"
            className="max-h-24 overflow-y-auto custom-scrollbar sm:max-h-none sm:overflow-visible"
          >
            {!toolsEnabled ? (
              // Nothing to select and no model to switch for, so the
              // tool and model guidance would only be noise here.
              <p className="mt-1 text-sm">
                Tools are disabled for this deployment, so messages run as normal chat.
              </p>
            ) : (
              <>
                {reduceHelpText ? (
                  <p className="mt-1 text-sm">
                    Select at least one tool to let the assistant take multiple steps.
                  </p>
                ) : (
                  <p className="mt-1 text-sm">
                    Agent Mode lets the assistant take multiple steps: it calls the tools you
                    select, inspects the results, and continues until the task is done. With no
                    tools selected, this chat behaves like normal chat.
                  </p>
                )}
                <p className={reduceHelpText ? 'mt-1 text-sm' : 'mt-1.5 text-sm'}>
                  You also need a tool-capable model.
                  {currentModelToolSupport === true && (
                    <> <strong>{currentModelLabel}</strong> supports tools.</>
                  )}
                  {currentModelToolSupport === false && (
                    <>
                      {' '}<strong>{currentModelLabel}</strong> does not support tools, so switch
                      to a tool-capable model to use Agent Mode.
                    </>
                  )}
                </p>
              </>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {toolsEnabled && (
              <button
                type="button"
                onClick={() => openSettingsPanel({ tab: 'tools' })}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md bg-amber-500 hover:bg-amber-400 text-gray-950 text-xs font-semibold transition-colors"
              >
                <Wrench className="w-3.5 h-3.5" aria-hidden="true" />
                Choose tools
              </button>
            )}
            <button
              type="button"
              onClick={onTurnOff}
              className="px-1.5 py-1 text-xs underline decoration-dotted hover:decoration-solid transition-colors"
            >
              Turn off Agent Mode
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
