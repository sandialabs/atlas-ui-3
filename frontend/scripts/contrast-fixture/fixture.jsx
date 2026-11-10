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
import { useToast } from '../../src/components/ui/toastContext'
import Header from '../../src/components/Header'
import Message from '../../src/components/Message'
import SettingsPanel from '../../src/components/SettingsPanel'
import AgentQuestionPrompt from '../../src/components/AgentQuestionPrompt'
import { ConversationTreeRow } from '../../src/components/ConversationTreeRow'
import { RetroWindow, RetroWorkbench, RetroButton } from '../../src/themes/RetroControls'
import { FixtureChatProvider } from './stubs/ChatContext'
import { installFixtureFetch, fixtureFetchesPending, FIXTURE_FILES } from './stubs/fetchFixtures'
import ComposerNotice from '../../src/components/ComposerNotice'
import AgentModeNoToolsBanner from '../../src/components/AgentModeNoToolsBanner'
import AgentRunStatus from '../../src/components/AgentRunStatus'
import EnabledToolsIndicator from '../../src/components/EnabledToolsIndicator'
import EnabledDataSourcesIndicator from '../../src/components/EnabledDataSourcesIndicator'
import LogViewer from '../../src/components/LogViewer'
import FilesPage from '../../src/components/FilesPage'
import AllFilesView from '../../src/components/AllFilesView'
import SessionFilesView from '../../src/components/SessionFilesView'
import TelemetryDashboard from '../../src/components/TelemetryDashboard'
import AdminDashboard from '../../src/components/AdminDashboard'
import AdminModal from '../../src/components/AdminModal'
import HelpPage from '../../src/components/HelpPage'
import MarketplacePanel from '../../src/components/MarketplacePanel'
import { WifiOff, Wrench, Image } from 'lucide-react'

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

// Composer state that shows every warning row and both indicator strips.
const COMPOSER_CHAT = {
  features: { tools: true, rag: true, chat_history: true, retro_themes: true },
  selectedTools: new Set(['calculator_evaluate', 'code_runner_run']),
  tools: [
    { server: 'calculator', description: 'Math', tools: ['evaluate'], tool_count: 1 },
    { server: 'code_runner', description: 'Run code', tools: ['run'], tool_count: 1 },
  ],
  ragSources: [{ serverName: 'docs', id: 'handbook', label: 'Team handbook' }],
  selectedDataSources: new Set(['docs:handbook']),
}
const PAGE_CHAT = {
  token: 'fixture', isInAdminGroup: true,
  tools: COMPOSER_CHAT.tools,
  prompts: [{ server: 'prompt_library', description: 'Shared prompts', prompts: [{ name: 'summarize', description: 'Summarize a document' }], prompt_count: 1 }],
  features: { ...COMPOSER_CHAT.features, marketplace: true },
}
const SESSION_FILES = {
  total_files: 2,
  files: FIXTURE_FILES.slice(0, 2).map(f => ({ ...f, name: f.filename, extension: f.filename.split('.').pop() })),
  categories: { document: [FIXTURE_FILES[0]], image: [FIXTURE_FILES[1]] },
}
const HEALTH = {
  title: 'System health',
  content: { type: 'health', overallStatus: 'warning', components: [
    { component: 'MCP servers', status: 'healthy', details: { connected: 1 } },
    { component: 'Chat history', status: 'warning' },
    { component: 'Object storage', status: 'error' },
  ] },
}
const MCP_HEALTH = {
  title: 'MCP health',
  content: { type: 'mcp-health', healthSummary: { overall_status: 'degraded', healthy_count: 1, total_count: 2, last_check: 1794340000, check_interval: 60, servers: {
    calculator: { status: 'healthy', last_check: 1794340000, response_time_ms: 12 },
    code_runner: { status: 'unhealthy', last_check: 1794340000, error_message: 'connection refused' },
  } } },
}

function ThemeHandle() {
  const { setTheme } = useTheme()
  useEffect(() => {
    window.__atlasContrastFixture = {
      setTheme(id) { flushSync(() => setTheme(id)) },
      // Pages load their (stubbed) data in effects; resolve once every
      // stubbed fetch has settled and React has committed the result.
      async ready() {
        for (let i = 0; i < 200; i++) {
          await new Promise(resolve => setTimeout(resolve, 25))
          if (fixtureFetchesPending() === 0 && i > 8) return true
        }
        return false
      },
    }
  }, [setTheme])
  return null
}

// Persistent toasts of every kind, one with an inline action.
function ToastSamples() {
  const toast = useToast()
  useEffect(() => {
    toast.success('Conversation saved', { duration: 0 })
    toast.error('Upload failed: report.pdf is too large', { duration: 0, action: { label: 'Retry', onClick: noop } })
    toast.info('Theme changed to Windows XP Luna', { duration: 0, action: { label: 'Undo', onClick: noop } })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
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
                {/* ChatArea composer plate with its warning rows (real components). */}
                <FixtureChatProvider value={COMPOSER_CHAT}>
                  <Region name="composer-notices" className="atlas-composer-plate p-4 border-t border-gray-700">
                    <ComposerNotice tone="info" role="status"><span>Choose a model to start chatting.</span></ComposerNotice>
                    {/* Remaining generated notice tones (used by toasts, admin status pills, log levels). */}
                    <ComposerNotice tone="success"><span>Success tone notice</span></ComposerNotice>
                    <ComposerNotice tone="alert"><span>Alert tone notice</span></ComposerNotice>
                    <ComposerNotice tone="agent"><span>Agent tone notice</span></ComposerNotice>
                    <ComposerNotice tone="danger" icon={WifiOff} role="status">
                      <span>Your session ended. <a href="#signin" className="underline font-medium hover:decoration-2">Sign in again</a> to continue chatting.</span>
                    </ComposerNotice>
                    <ComposerNotice tone="warning" icon={Wrench}><span><strong>Mock model</strong> does not support tool/function calling.</span></ComposerNotice>
                    <ComposerNotice tone="warning" icon={Image}><span><strong>Mock model</strong> does not support vision/image input.</span></ComposerNotice>
                    <AgentRunStatus isAgentRunning currentAgentStep={2} onStop={noop} />
                    <AgentRunStatus isAgentRunning waitingForAnswer onStop={noop} />
                    <EnabledToolsIndicator />
                    <EnabledDataSourcesIndicator />
                    <AgentModeNoToolsBanner toolsEnabled currentModelToolSupport={false} currentModelLabel="Mock model" onTurnOff={noop} />
                    <AgentModeNoToolsBanner toolsEnabled={false} onTurnOff={noop} />
                  </Region>
                </FixtureChatProvider>
                {/* Routed pages and admin views outside the chat. */}
                <FixtureChatProvider value={PAGE_CHAT}>
                  <Region name="page-logviewer"><LogViewer /></Region>
                  <Region name="page-files"><FilesPage /></Region>
                  <Region name="files-panel-all" className="bg-gray-800"><AllFilesView /></Region>
                  <Region name="files-panel-session" className="bg-gray-800">
                    <SessionFilesView files={SESSION_FILES} onDownloadFile={noop} onDeleteFile={noop} taggedFiles={new Set()} onToggleFileTag={noop} />
                  </Region>
                  {['overview', 'tools', 'llm', 'rag', 'session'].map(tab => (
                    <Region key={tab} name={`page-telemetry-${tab}`}><TelemetryDashboard initialTab={tab} /></Region>
                  ))}
                  <Region name="page-admin"><AdminDashboard /></Region>
                  <Region name="admin-modal-health"><AdminModal data={HEALTH} onClose={noop} onSave={noop} onDownload={noop} addNotification={noop} /></Region>
                  <Region name="admin-modal-mcp"><AdminModal data={MCP_HEALTH} onClose={noop} onSave={noop} onDownload={noop} addNotification={noop} /></Region>
                  <Region name="page-help"><HelpPage /></Region>
                  <Region name="page-marketplace"><MarketplacePanel /></Region>
                </FixtureChatProvider>
                <Region name="toasts"><ToastProvider><ToastSamples /></ToastProvider></Region>
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
installFixtureFetch()
const root = createRoot(document.getElementById('fixture-root'))
flushSync(() => root.render(<Fixture />))
