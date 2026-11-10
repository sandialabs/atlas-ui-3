/* eslint-disable react-refresh/only-export-components -- build-only contrast fixture, never hot-reloaded */
/**
 * Browser contrast fixture (PR #1175 review): mounts the shipped Atlas
 * components that carry themed text, so check-retro-browser.py measures the
 * real markup and classes instead of hand-copied class names.
 *
 * Built as one classic (IIFE) script by ./vite.config.js; the checker inlines
 * it next to the production stylesheet from `npm run build` and then calls
 * window.__atlasContrastFixture.setTheme(id) for every registered theme, so
 * theme-dependent markup (retro window glyphs, Settings captions) re-renders.
 *
 * Contexts that need a backend are replaced by inert stubs (./stubs); nothing
 * here fetches, opens a socket, or executes tools.
 */
import { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider, useTheme } from '../../src/contexts/ThemeContext'
import { ToastProvider, DialogProvider } from '../../src/components/ui/ToastProvider'
import Header from '../../src/components/Header'
import Message from '../../src/components/Message'
import SettingsPanel from '../../src/components/SettingsPanel'
import AgentQuestionPrompt from '../../src/components/AgentQuestionPrompt'
import { ConversationTreeRow } from '../../src/components/ConversationTreeRow'
import { RetroWindow, RetroWorkbench, RetroButton } from '../../src/themes/RetroControls'
import { FixtureChatProvider } from './stubs/ChatContext'

const noop = () => {}

const LOGS = ['error', 'warning', 'alert', 'info', 'notice', 'debug', 'trace'].map(level => ({
  role: 'system', type: 'tool_log', log_level: level,
  content: `${level} log line from the calculator tool`,
}))

const TRANSCRIPT = [
  { role: 'user', content: 'Show me a link and a short code sample.' },
  {
    role: 'assistant',
    content: 'Here is the [theme registry doc](https://example.invalid/docs) and code:\n\n'
      + '```javascript\nconst theme = "win95" // comment\nrender({ id: theme, samples: 55 })\n```',
  },
  {
    role: 'system', type: 'tool_call', tool_name: 'calculator_evaluate', server_name: 'calculator',
    status: 'completed', arguments: { expression: '6 * 7' }, result: '{"result": 42}',
  },
  {
    role: 'system', type: 'tool_call', tool_name: 'calculator_evaluate', server_name: 'calculator',
    status: 'failed', arguments: { expression: '1 / 0' }, result: 'ZeroDivisionError: division by zero',
  },
  {
    role: 'system', type: 'tool_call', tool_name: 'atlas_sleep', server_name: 'atlas',
    status: 'interrupted', arguments: { seconds: 2 }, result: 'Stopped by the user',
  },
  ...LOGS,
  { role: 'system', type: 'system', subtype: 'file-attaching', text: 'Attaching report.pdf...' },
  { role: 'system', type: 'system', subtype: 'file-attached', text: 'Attached report.pdf' },
  { role: 'system', type: 'system', subtype: 'file-attach-error', text: 'Could not attach notes.bin' },
  { role: 'system', type: 'system', subtype: 'other', content: 'System notice: model switched' },
  { role: 'system', content: 'Plain system notice' },
  { role: 'system', type: 'agent_status', content: 'Agent step 2 of 5' },
  { role: 'system', type: 'agent_reason', content: 'Reasoning: compare the two results first.' },
]

const APPROVAL = {
  role: 'system', type: 'tool_approval_request', status: 'pending',
  tool_call_id: 'call-fixture', tool_name: 'calculator_evaluate', server_name: 'calculator',
  arguments: { expression: '2 ** 10', precision: 4 }, allow_edit: true,
  admin_required: false,
}

const CONVERSATIONS = [
  { id: 'c1', title: 'Quarterly numbers', preview: 'What were the totals?', updated_at: '2026-11-10T14:00:00Z', message_count: 12, _depth: 0, _childCount: 0 },
  { id: 'c2', title: 'Theme review', preview: 'Show me the gallery', updated_at: '2026-11-09T10:00:00Z', message_count: 4, _depth: 0, _childCount: 2 },
  { id: 'c3', title: 'Sub-run', preview: 'Child conversation', updated_at: '2026-11-08T09:00:00Z', message_count: 2, _depth: 1, _childCount: 0 },
]
const RUNS = ['queued', 'running', 'waiting_for_input', 'failed', 'cancelled']

function ThemeHandle() {
  const { setTheme } = useTheme()
  useEffect(() => {
    window.__atlasContrastFixture = {
      setTheme(id) { flushSync(() => setTheme(id)) },
    }
  }, [setTheme])
  return null
}

function Region({ name, className = '', children }) {
  return <section data-contrast-region={name} className={className}>{children}</section>
}

function Fixture() {
  return (
    <ThemeProvider>
      <ThemeHandle />
      <MemoryRouter>
        <ToastProvider>
          <DialogProvider>
            <FixtureChatProvider value={{}}>
              {/* App.jsx desktop shell classes, so retro desktop fills apply. */}
              <div className="atlas-theme-desktop flex flex-col w-full bg-gray-900 text-gray-200">
                <Region name="header">
                  <Header
                    onToggleSidebar={noop} onToggleRag={noop} onToggleFiles={noop}
                    onToggleCanvas={noop} onCloseCanvas={noop} onToggleSettings={noop}
                  />
                </Region>
                <div className="flex">
                  {/* Sidebar.jsx <aside> surface around the real conversation rows. */}
                  <Region name="conversation-metadata" className="bg-gray-800 border-r border-gray-700 w-72">
                    <div className="py-1" role="list" aria-label="Conversations">
                      {CONVERSATIONS.map((conv, i) => (
                        <ConversationTreeRow
                          key={conv.id} conv={conv} isActive={i === 0} isGroupCollapsed={false}
                          shownRun={i === 1 ? { status: 'running' } : null}
                          classification={i === 0 ? 'Public' : null} showClassification={i === 0}
                          formatDate={value => value.slice(0, 10)}
                          onOpen={noop} onContextMenu={noop} onToggleCollapsed={noop}
                        />
                      ))}
                      {RUNS.map(status => (
                        <ConversationTreeRow
                          key={status}
                          conv={{ id: status, title: `Run ${status}`, preview: '', updated_at: '2026-11-07T00:00:00Z', message_count: 1, _depth: 0, _childCount: 0 }}
                          isActive={false} isGroupCollapsed={false} shownRun={{ status }}
                          formatDate={value => value.slice(0, 10)}
                          onOpen={noop} onContextMenu={noop} onToggleCollapsed={noop}
                        />
                      ))}
                    </div>
                  </Region>
                  <main className="flex-1 min-w-0 p-4 space-y-4">
                    <Region name="transcript" className="space-y-4">
                      {TRANSCRIPT.map((message, index) => <Message key={index} message={message} />)}
                    </Region>
                    <Region name="approval-prompt-compact">
                      <Message message={APPROVAL} />
                    </Region>
                    <FixtureChatProvider value={{ settings: { compactMessages: false } }}>
                      <Region name="approval-prompt-classic" className="space-y-4">
                        <Message message={{ ...APPROVAL, tool_call_id: 'call-classic' }} />
                        <Message message={TRANSCRIPT[2]} />
                        <Message message={LOGS[0]} />
                      </Region>
                    </FixtureChatProvider>
                    <Region name="agent-input">
                      <AgentQuestionPrompt question="Which report should I summarize?" onAnswer={noop} />
                    </Region>
                    <Region name="retro-controls">
                      <RetroWindow title="Fixture window">
                        <RetroButton>Plain action</RetroButton>
                        <RetroButton variant="primary">Primary action</RetroButton>
                        <RetroButton disabled>Disabled action</RetroButton>
                      </RetroWindow>
                      <RetroWorkbench />
                    </Region>
                  </main>
                </div>
                <Region name="settings">
                  <SettingsPanel isOpen onClose={noop} initialTab="general" />
                </Region>
              </div>
            </FixtureChatProvider>
          </DialogProvider>
        </ToastProvider>
      </MemoryRouter>
    </ThemeProvider>
  )
}

// Expand tool-call details (input arguments + output) like a user who opened them.
try { localStorage.setItem('toolDetailsCollapsed', 'false') } catch { /* file:// storage */ }
const root = createRoot(document.getElementById('fixture-root'))
flushSync(() => root.render(<Fixture />))
