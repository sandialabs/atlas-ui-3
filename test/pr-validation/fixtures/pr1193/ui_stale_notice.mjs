#!/usr/bin/env node
/**
 * Visual driver for issue #1193: the "Answer in progress" notice a reopened
 * conversation shows is retired once the view has moved on to newer steps.
 *
 * Boots a scripted mock model (fixtures/pr1193/mock_llm.py: the pr981 mock
 * plus a `think=` pause between steps) and a real backend, then drives the
 * built frontend with Playwright:
 *
 *   1. Tab A starts a six-step atlas_sleep agent run with auto-approve on.
 *   2. Tab B opens the conversation from history after the first step has
 *      completed and before the second starts. It shows the notice.
 *   3. The second step reaches tab B through the mid-run poll: the notice is
 *      gone, and the agent-run status and busy row still show the run.
 *   4. Tab B navigates away and back, then every remaining step is sampled:
 *      the notice never returns while no answer text is open.
 *   5. The final answer streams: the partial bubble carries the notice again,
 *      because that segment really is incomplete.
 *   6. The run ends: one final answer, six tool rows, no notice.
 *
 * Screenshots are written beside this script (joined-initial.png,
 * joined-advanced.png, joined-partial-answer.png, joined-final.png).
 *
 * The frontend is rebuilt first (opt out with SKIP_UI_BUILD=1). Playwright
 * comes from test_e2e (`cd test_e2e && npm install && npx playwright install
 * chromium`), or from PLAYWRIGHT_MJS; the Python interpreter is `python`, or
 * ATLAS_PY. Run from the repo root:
 *
 *   node test/pr-validation/fixtures/pr1193/ui_stale_notice.mjs
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '../../../..')
const PY = process.env.ATLAS_PY || 'python'
const { chromium } = await import(
  process.env.PLAYWRIGHT_MJS || join(ROOT, 'test_e2e/node_modules/playwright/index.mjs')
)
// Model, MCP and RAG config are the pr981 fixtures; only the mock differs.
const FIXTURES = join(ROOT, 'test/pr-validation/fixtures/pr981')
const WORK = mkdtempSync(join(tmpdir(), 'pr1193-ui-'))
const NOTICE = 'Answer in progress — it will refresh when the response finishes.'

const freePort = () => new Promise((resolve) => {
  const s = createServer()
  s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)) })
})
const waitFor = async (url, tries = 60) => {
  for (let i = 0; i < tries; i++) {
    try { const r = await fetch(url); if (r.ok) return true } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 1000))
  }
  return false
}
const sleep = ms => new Promise(r => setTimeout(r, ms))

const MOCK_PORT = await freePort()
const ATLAS_PORT = await freePort()
const procs = []
const run = (cmd, args, opts) => { const p = spawn(cmd, args, { stdio: 'inherit', ...opts }); procs.push(p); return p }
let tabA = null
let tabB = null
let failed = 0
const check = (ok, label) => { console.log(`${ok ? 'PASS' : 'FAIL'}: ${label}`); if (!ok) failed += 1 }

try {
  if (!process.env.SKIP_UI_BUILD) {
    console.log('building frontend (set SKIP_UI_BUILD=1 to reuse dist/)')
    await new Promise((resolve, reject) => {
      const p = spawn('npm', ['run', 'build'], { stdio: 'inherit', cwd: join(ROOT, 'frontend') })
      p.on('exit', code => (code === 0 ? resolve() : reject(new Error(`npm run build exited ${code}`))))
      p.on('error', reject)
    })
  }
  run(PY, [join(HERE, 'mock_llm.py')], { env: { ...process.env, MOCK_LLM_PORT: String(MOCK_PORT) } })
  const configDir = join(WORK, 'config')
  for (const d of ['config', 'data', 'logs']) mkdirSync(join(WORK, d), { recursive: true })
  for (const f of ['mcp.json', 'rag-sources.json']) writeFileSync(join(configDir, f), readFileSync(join(FIXTURES, f)))
  writeFileSync(
    join(configDir, 'llmconfig.yml'),
    readFileSync(join(FIXTURES, 'llmconfig.yml'), 'utf8').replaceAll('__MOCK_LLM_PORT__', String(MOCK_PORT)),
  )
  run(PY, ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(ATLAS_PORT)], {
    cwd: join(ROOT, 'atlas'),
    env: {
      ...process.env,
      DEBUG_MODE: 'true',
      FEATURE_CHAT_HISTORY_ENABLED: 'true',
      FEATURE_AGENT_MODE_AVAILABLE: 'true',
      FEATURE_TOOLS_ENABLED: 'true',
      FEATURE_RAG_ENABLED: 'false',
      FEATURE_WORKSPACES_ENABLED: 'false',
      USE_MOCK_S3: 'true',
      REQUIRE_TOOL_APPROVAL_BY_DEFAULT: 'false',
      FORCE_TOOL_APPROVAL_GLOBALLY: 'false',
      CHAT_HISTORY_DB_URL: `duckdb:///${WORK}/data/chat_history.db`,
      APP_LOG_DIR: join(WORK, 'logs'),
      APP_CONFIG_DIR: configDir,
      PYTHONPATH: ROOT,
    },
  })
  if (!(await waitFor(`http://127.0.0.1:${ATLAS_PORT}/api/health`))) throw new Error('backend did not come up')
  if (!(await waitFor(`http://127.0.0.1:${MOCK_PORT}/health`))) throw new Error('mock model did not come up')
  const base = `http://127.0.0.1:${ATLAS_PORT}`
  const served = await (await fetch(base + '/')).text()
  console.log('served bundle:', served.match(/assets\/index-[A-Za-z0-9_-]*\.js/)?.[0])

  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('chatui-settings', JSON.stringify({ autoApproveTools: true }))
      localStorage.setItem('chatui-save-mode', JSON.stringify('server'))
      localStorage.setItem('chatui-agent-mode-enabled', JSON.stringify(true))
      localStorage.setItem('chatui-selected-tools', JSON.stringify(['atlas_canvas', 'atlas_sleep']))
    } catch { /* ignore */ }
  })
  tabA = await ctx.newPage()
  tabB = await ctx.newPage()
  for (const [name, pg] of [['A', tabA], ['B', tabB]]) {
    pg.on('console', (m) => { if (/Atlas v|pageerror/i.test(m.text())) console.log(`[console ${name}]`, m.text().slice(0, 200)) })
    pg.on('pageerror', (e) => console.log(`[pageerror ${name}]`, String(e).slice(0, 200)))
  }

  await tabA.goto(base)
  await tabA.getByRole('textbox', { name: /Type a message/ }).waitFor()
  await tabB.goto(base)
  await tabB.getByRole('textbox', { name: /Type a message/ }).waitFor()

  const STEPS = 6
  const composer = tabA.getByRole('textbox', { name: /Type a message/ })
  await composer.fill(`label=UI1193 sleep=1 steps=${STEPS} think=5 words=30 stream_ms=400`)
  await composer.press('Enter')

  const doneA = tabA.locator('.chat-messages [role="img"][aria-label="SUCCESS"]')
  await doneA.nth(0).waitFor({ timeout: 40000 })
  console.log('tab A: first tool step completed; agent is thinking about step 2')

  // Tab B joins between steps: one finished tool call, no open segment.
  await tabB.getByText(/UI1193/).first().click({ timeout: 15000 })
  const chatB = tabB.locator('.chat-messages')
  const doneB = chatB.locator('[role="img"][aria-label="SUCCESS"]')
  const notice = chatB.getByText(NOTICE)
  const runOnly = chatB.getByTestId('stream-replay-run-active')
  const runStatus = tabB.getByTestId('agent-run-status')
  await doneB.nth(0).waitFor({ timeout: 15000 })
  const firstCount = await doneB.count()
  let sawInitial = false
  for (let i = 0; i < 10 && !sawInitial; i++) {
    sawInitial = (await notice.count()) > 0
    if (!sawInitial) await sleep(200)
  }
  console.log(`tab B joined with ${firstCount} completed step(s); initial notice visible: ${sawInitial}`)
  check(sawInitial || firstCount > 1, 'initial reattachment shows the notice (or had already advanced)')
  await tabB.screenshot({ path: join(HERE, 'joined-initial.png') })

  // The next completed step reaches tab B through the mid-run poll.
  await doneB.nth(firstCount).waitFor({ timeout: 30000 })
  await sleep(500)
  check((await notice.count()) === 0, `notice retired after step ${firstCount + 1} was reconciled`)
  check((await runOnly.count()) === 0, 'no extra run-only line while the agent-run status is shown')
  check(await runStatus.isVisible(), 'agent-run status still visible mid-run')
  check((await chatB.getByTestId('agent-busy-indicator').count()) === 1, 'transcript busy row still visible mid-run')
  await tabB.screenshot({ path: join(HERE, 'joined-advanced.png') })

  // Navigate away and back in the joined tab: a fresh placeholder, retired
  // again at the next step.
  await tabB.getByRole('button', { name: /new chat/i }).first().click().catch(() => {})
  await sleep(300)
  await tabB.getByText(/UI1193/).first().click({ timeout: 15000 })
  await doneB.nth(0).waitFor({ timeout: 15000 })
  const backCount = await doneB.count()
  console.log(`tab B navigated away and back with ${backCount} completed step(s); notice: ${await notice.count()}`)

  // Sample until every step has completed: the notice must not come back
  // unless a text bubble is genuinely open.
  let stale = 0
  let samples = 0
  let advancedAfterReturn = false
  const deadline = Date.now() + 90000
  while (Date.now() < deadline) {
    const n = await doneB.count()
    const hasText = (await chatB.getByText(/FINAL\[UI1193\]/).count()) > 0
    if (hasText) break
    if (n > backCount) {
      if (!advancedAfterReturn) { advancedAfterReturn = true; await sleep(400) }
      samples += 1
      if ((await notice.count()) > 0 && (await chatB.getByText(/FINAL\[UI1193\]/).count()) === 0) stale += 1
    }
    await sleep(500)
  }
  check(advancedAfterReturn, 'run advanced after navigating away and back')
  check(stale === 0, `no stale notice across ${samples} samples of later steps`)

  // The final answer streams: a genuinely incomplete segment keeps the notice.
  await chatB.getByText(/FINAL\[UI1193\]/).first().waitFor({ timeout: 60000 })
  let sawPartial = false
  for (let i = 0; i < 20 && !sawPartial; i++) {
    const done = (await chatB.getByText(/END\[UI1193\]/).count()) > 0
    if (done) break
    sawPartial = (await notice.count()) > 0
    if (!sawPartial) await sleep(200)
  }
  check(sawPartial, 'a genuinely incomplete streamed answer is marked in progress')
  check((await doneB.count()) === STEPS, `all ${STEPS} tool steps visible before the run ended`)
  await tabB.screenshot({ path: join(HERE, 'joined-partial-answer.png') })

  await chatB.getByText(/END\[UI1193\]/).first().waitFor({ timeout: 60000 })
  await sleep(4000)
  check((await notice.count()) === 0 && (await runOnly.count()) === 0, 'no notice after the run completed')
  check((await runStatus.count()) === 0, 'agent-run status gone after completion')
  check((await chatB.getByText(/FINAL\[UI1193\]/).count()) === 1, 'exactly one final answer bubble (no duplicate)')
  check((await chatB.getByText(/END\[UI1193\]/).count()) === 1, 'final answer complete, no lost text')
  check((await doneB.count()) === STEPS, `still exactly ${STEPS} completed tool rows`)
  await tabB.screenshot({ path: join(HERE, 'joined-final.png') })

  await browser.close()
  console.log(failed ? `RESULT: ${failed} check(s) failed` : 'RESULT: all checks passed')
  process.exitCode = failed ? 1 : 0
} catch (err) {
  try {
    if (tabA) await tabA.screenshot({ path: join(WORK, 'debug-tabA.png') })
    if (tabB) await tabB.screenshot({ path: join(WORK, 'debug-tabB.png') })
    console.log(`debug screenshots in ${WORK}`)
  } catch { /* best effort */ }
  console.error(err)
  process.exitCode = 1
} finally {
  for (const p of procs) p.kill()
}
