/**
 * Canned, synthetic API responses for the routed pages the contrast fixture
 * mounts (LogViewer, Files, Telemetry, Admin, Help). Shapes match the real
 * endpoints (captured from a local dev server, then rewritten by hand); no
 * network is used. Every response resolves immediately; unknown URLs get {}.
 */
const NS = 1794340000000000000
const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
const LOG_ENTRIES = LEVELS.map((level, i) => ({
  timestamp: `2026-11-10T14:0${i}:00.000000+00:00`, level,
  module: i % 2 ? 'service' : 'mcp_client', logger: 'atlas.application.chat.service',
  function: 'handle_turn', message: `${level.toLowerCase()} sample: calculator_evaluate finished in ${i + 1}2 ms`,
  trace_id: 'f'.repeat(32), span_id: 'e'.repeat(16), line: 100 + i, thread_name: 'MainThread', extras: {},
}))
const now = '2026-11-10T14:00:00Z'
export const FIXTURE_FILES = [
  { key: 'users/reviewer/report.pdf', filename: 'report.pdf', size: 482113, type: 'application/pdf', content_type: 'application/pdf', last_modified: now, source: 'user', tags: {} },
  { key: 'users/reviewer/chart.png', filename: 'chart.png', size: 91234, type: 'image/png', content_type: 'image/png', last_modified: now, source: 'tool', source_tool: 'plot_chart', tags: {} },
  { key: 'users/reviewer/data.csv', filename: 'data.csv', size: 2048, type: 'text/csv', content_type: 'text/csv', last_modified: now, source: 'user', tags: {} },
  { key: 'users/reviewer/script.py', filename: 'script.py', size: 1200, type: 'text/x-python', content_type: 'text/x-python', last_modified: now, source: 'tool', source_tool: 'code_runner', tags: {} },
]
const ROUTES = [
  [/^\/admin\/logs\/viewer/, () => ({ entries: LOG_ENTRIES })],
  [/^\/api\/users\/[^/]+\/files\/stats/, () => ({ total_files: 4, total_size: 576595, upload_count: 2, generated_count: 2 })],
  [/^\/api\/files(\?|$)/, () => FIXTURE_FILES, { 'X-Total-Count': String(FIXTURE_FILES.length) }],
  [/^\/admin\/telemetry\/overview/, () => ({ range: '24h', since_ns: NS - 864e11, until_ns: NS, turns: 18, sessions: 16, tool_calls: 9, tool_success_rate: 0.78, llm_calls: 21, llm_latency_p50_ms: 499, llm_latency_p95_ms: 794, llm_retries_total: 1, rag_queries: 3 })],
  [/^\/admin\/telemetry\/tools\/[^/]+\/failures/, () => ({ failures: [] })],
  [/^\/admin\/telemetry\/tools/, () => ({ range: '24h', tools: [
    { tool_name: 'calculator_evaluate', call_count: 7, success_rate: 1, failure_count: 0, duration_p50_ms: 112, duration_p95_ms: 131, last_failure_start_ns: null, last_failure_error_type: null },
    { tool_name: 'code_runner', call_count: 2, success_rate: 0, failure_count: 2, duration_p50_ms: 900, duration_p95_ms: 1300, last_failure_start_ns: NS, last_failure_error_type: 'TimeoutError' },
  ] })],
  [/^\/admin\/telemetry\/llm/, () => ({ range: '24h', models: [
    { model: 'openai/review-mock', call_count: 12, latency_p50_ms: 502, latency_p95_ms: 653, latency_p99_ms: 765, input_tokens_total: 1200, output_tokens_total: 800, total_tokens_total: 2000, retry_count_total: 1, retry_rate: 0.08, error_count: 1 },
  ] })],
  [/^\/admin\/telemetry\/rag/, () => ({ range: '24h', sources: [] })],
  [/^\/admin\/telemetry\/status/, () => ({ backend: 'FileSpanReader', path: 'logs/spans.jsonl', available: true, size_bytes: 41121, last_modified: NS / 1e9 })],
  [/^\/admin\/mcp\/status/, () => ({ connected_servers: ['calculator'], configured_servers: ['calculator', 'code_runner'], failed_servers: { code_runner: 'connection refused' }, mcp_config_path: 'config/mcp.json', auto_reconnect: { enabled: false, base_interval: 60, max_interval: 300, backoff_multiplier: 2, running: false }, tool_counts: { calculator: 1 }, prompt_counts: { calculator: 0 } })],
  [/^\/admin\/help-config/, () => ({ content: '# Help', file_path: 'config/help.md', mode: 'local', requested_mode: 'local', content_url: '', error: null, external: false })],
  [/^\/api\/feedback\/stats/, () => ({ total_feedback: 3, rating_distribution: { positive: 1, neutral: 1, negative: 1 }, average_rating: 0, recent_feedback: 3 })],
  [/^\/api\/feedback/, () => ({ feedback: [], pagination: { total: 0, limit: 100, offset: 0, has_more: false }, statistics: { positive: 0, neutral: 0, negative: 0, total: 0, average: 0 } })],
  [/^\/api\/help/, () => ({ mode: 'local', requested_mode: 'local', content: '# Help\n\n## Quick Start\n\nClick **New Chat** or press `Ctrl+Alt+N` to start. See the [admin guide](https://example.invalid/admin).\n\n- Tools live in **Settings**\n- Files open from the header\n\n```bash\nnpm run build\n```\n' })],
  [/^\/admin\/?$/, () => ({ message: 'Admin Dashboard', user: 'reviewer@example.com', available_endpoints: ['/admin/logs/viewer', '/admin/mcp/status'] })],
]

let pending = 0
export function installFixtureFetch() {
  window.fetch = async (input) => {
    pending += 1
    try {
      const url = typeof input === 'string' ? input : input.url
      const path = url.replace(/^https?:\/\/[^/]+/, '')
      const route = ROUTES.find(([pattern]) => pattern.test(path))
      const body = route ? route[1]() : {}
      return new Response(JSON.stringify(body), {
        status: 200, headers: { 'Content-Type': 'application/json', ...(route?.[2] || {}) },
      })
    } finally {
      pending -= 1
    }
  }
}
export const fixtureFetchesPending = () => pending
