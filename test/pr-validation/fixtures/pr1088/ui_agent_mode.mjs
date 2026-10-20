#!/usr/bin/env node
/**
 * Browser driver for PR #1088: Agent Mode clarity and run visibility.
 *
 * Boots the scripted tool-calling mock model from fixtures/pr1074 and two real
 * backends (tools enabled, tools disabled), then drives the built frontend
 * with Playwright and asserts:
 *
 *   1. Agent Mode on with no tools selected shows the expanded callout: title,
 *      the plain-language explanation, the tool-capable model line, and both
 *      actions. "Choose tools" opens Settings on the Tools tab.
 *   2. The header help button opens "What Agent Mode does", Escape closes it
 *      and returns focus to the button.
 *   3. On a short phone viewport the callout's title, "Choose tools", the
 *      message box, and Send all stay on screen.
 *   4. "Turn off Agent Mode" removes the callout and flips the toggle.
 *   5. "Reduce helper text" removes the help button and shortens the callout
 *      copy, but keeps the state and both actions.
 *   6. A running agent (atlas_sleep tool steps) shows the footer run status
 *      with its step count; it stays visible while the transcript is scrolled
 *      to the top, and its Stop ends the run.
 *   7. With FEATURE_TOOLS_ENABLED=false the callout and the help popover say
 *      tools are disabled and offer no "Choose tools".
 *
 * Screenshots go to $PR1088_SCREENSHOT_DIR when set (nothing is written into
 * the repo by default).
 *
 * Prerequisites: `cd test_e2e && npm install && npx playwright install
 * chromium`, and the frontend built into atlas/static or frontend/dist. Run
 * from the repo root with the venv active:
 *
 *   node test/pr-validation/fixtures/pr1088/ui_agent_mode.mjs
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '../../../../test_e2e/node_modules/playwright/index.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../../../..')
const MOCK_FIXTURES = join(ROOT, 'test/pr-validation/fixtures/pr1074')
const WORK = mkdtempSync(join(tmpdir(), 'pr1088-ui-'))
const SHOTS = process.env.PR1088_SCREENSHOT_DIR || ''

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

const check = (cond, msg) => { if (!cond) throw new Error(`FAIL: ${msg}`) }
const pass = (msg) => console.log(`PASS: ${msg}`)
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: join(SHOTS, `${name}.png`) }) }

const procs = []
const run = (cmd, args, opts) => {
  const p = spawn(cmd, args, { stdio: 'inherit', ...opts })
  procs.push(p)
  return p
}

const startBackend = async (name, port, mockPort, toolsEnabled) => {
  const dir = join(WORK, name)
  const configDir = join(dir, 'config')
  for (const d of [configDir, join(dir, 'data'), join(dir, 'logs')]) mkdirSync(d, { recursive: true })
  for (const f of ['mcp.json', 'rag-sources.json']) {
    writeFileSync(join(configDir, f), readFileSync(join(MOCK_FIXTURES, f)))
  }
  writeFileSync(
    join(configDir, 'llmconfig.yml'),
    readFileSync(join(MOCK_FIXTURES, 'llmconfig.yml'), 'utf8').replaceAll('__MOCK_LLM_PORT__', String(mockPort)),
  )
  run('python', ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: join(ROOT, 'atlas'),
    env: {
      ...process.env,
      DEBUG_MODE: 'true',
      FEATURE_CHAT_HISTORY_ENABLED: 'false',
      FEATURE_AGENT_MODE_AVAILABLE: 'true',
      FEATURE_TOOLS_ENABLED: toolsEnabled ? 'true' : 'false',
      FEATURE_RAG_ENABLED: 'false',
      FEATURE_WORKSPACES_ENABLED: 'false',
      USE_MOCK_S3: 'true',
      LLM_MAX_RETRIES: '0',
      REQUIRE_TOOL_APPROVAL_BY_DEFAULT: 'false',
      FORCE_TOOL_APPROVAL_GLOBALLY: 'false',
      APP_LOG_DIR: join(dir, 'logs'),
      APP_CONFIG_DIR: configDir,
      PYTHONPATH: ROOT,
    },
  })
  if (!(await waitFor(`http://127.0.0.1:${port}/api/health`))) throw new Error(`${name} backend did not come up`)
}

// Seed browser state before the app reads it, then reload so it takes effect.
const seed = async (page, base, { tools, settings = {} }) => {
  await page.goto(base)
  await page.evaluate(({ tools, settings }) => {
    localStorage.setItem('chatui-agent-mode-enabled', 'true')
    localStorage.setItem('chatui-selected-tools', JSON.stringify(tools))
    localStorage.setItem('chatui-settings', JSON.stringify({ autoApproveTools: true, ...settings }))
  }, { tools, settings })
  await page.reload()
  await page.getByRole('textbox', { name: /Type a message/ }).waitFor()
}

const inViewport = async (locator) => {
  const box = await locator.boundingBox()
  const vp = locator.page().viewportSize()
  return !!box && box.y >= 0 && box.y + box.height <= vp.height && box.x >= 0 && box.x + box.width <= vp.width
}

let page = null
try {
  const MOCK_PORT = await freePort()
  const ATLAS_PORT = await freePort()
  const NO_TOOLS_PORT = await freePort()
  run('python', [join(MOCK_FIXTURES, 'mock_llm.py')], { env: { ...process.env, MOCK_LLM_PORT: String(MOCK_PORT) } })
  if (!(await waitFor(`http://127.0.0.1:${MOCK_PORT}/health`))) throw new Error('mock model did not come up')
  await startBackend('tools', ATLAS_PORT, MOCK_PORT, true)
  await startBackend('notools', NO_TOOLS_PORT, MOCK_PORT, false)
  console.log('mock model and both backends are up')

  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } })
  page = await ctx.newPage()
  const base = `http://127.0.0.1:${ATLAS_PORT}`

  // 1. No tools selected -> expanded callout.
  await seed(page, base, { tools: [] })
  const callout = page.getByTestId('agent-mode-no-tools-banner')
  await callout.waitFor()
  check(await callout.getByText('Agent Mode is on, but no tools are selected').isVisible(), 'callout title')
  check(await callout.getByText(/take multiple steps: it calls the tools you\s+select/).isVisible(), 'callout explanation')
  check(await callout.getByText(/supports tools\./).isVisible(), 'tool-capable model line names a supporting model')
  check(await callout.getByRole('button', { name: 'Choose tools' }).isVisible(), 'Choose tools action')
  check(await callout.getByRole('button', { name: 'Turn off Agent Mode' }).isVisible(), 'Turn off action')
  await shot(page, '1-no-tools-callout-desktop')
  pass('Agent Mode with no tools shows the expanded callout with explanation, model line, and both actions')

  await callout.getByRole('button', { name: 'Choose tools' }).click()
  const toolsTab = page.getByRole('tab', { name: /Tools & Integrations/ })
  await toolsTab.waitFor()
  check((await toolsTab.getAttribute('aria-selected')) === 'true', 'Settings opened on the Tools tab')
  await page.keyboard.press('Escape')
  await toolsTab.waitFor({ state: 'detached', timeout: 5000 })
  pass('"Choose tools" opens Settings on the Tools & Integrations tab')

  // 2. Header help popover.
  const helpButton = page.getByRole('button', { name: 'What Agent Mode does' })
  await helpButton.click()
  const helpDialog = page.getByRole('dialog', { name: 'What Agent Mode does' })
  await helpDialog.waitFor()
  check(await helpDialog.getByText(/needs a model that supports\s+tool calling/).isVisible(), 'help explains model requirement')
  check(await helpDialog.getByRole('button', { name: 'Choose tools' }).isVisible(), 'help offers Choose tools')
  await shot(page, '2-help-popover')
  await page.keyboard.press('Escape')
  await helpDialog.waitFor({ state: 'detached', timeout: 5000 })
  check(await helpButton.evaluate(el => el === document.activeElement), 'focus returns to the help button')
  // Keyboard path through the Close button returns focus too.
  await helpButton.press('Enter')
  await helpDialog.waitFor()
  await helpDialog.getByRole('button', { name: 'Close' }).focus()
  await page.keyboard.press('Enter')
  await helpDialog.waitFor({ state: 'detached', timeout: 5000 })
  check(await helpButton.evaluate(el => el === document.activeElement), 'Close returns focus to the help button')
  pass('Help popover opens, explains Agent Mode, closes on Escape or Close, and returns focus')

  // 3. Short phone viewport keeps the composer reachable.
  await page.setViewportSize({ width: 390, height: 600 })
  await page.waitForTimeout(300)
  const textbox = page.getByRole('textbox', { name: /Type a message/ })
  check(await inViewport(callout.getByText('Agent Mode is on, but no tools are selected')), 'callout title on screen at 390x600')
  check(await inViewport(callout.getByRole('button', { name: 'Choose tools' })), 'Choose tools on screen at 390x600')
  check(await inViewport(textbox), 'message box on screen at 390x600')
  check(await inViewport(page.getByTestId('send-button')), 'Send on screen at 390x600')
  await shot(page, '3-no-tools-callout-390x600')
  pass('At 390x600 the callout title, Choose tools, message box, and Send all stay on screen')
  await page.setViewportSize({ width: 1400, height: 900 })

  // 4. Turn off from the callout.
  const toggle = page.getByRole('button', { name: /^Agent Mode: (on|off)$/ })
  check((await toggle.getAttribute('aria-pressed')) === 'true', 'toggle reports on before turning off')
  await callout.getByRole('button', { name: 'Turn off Agent Mode' }).click()
  await callout.waitFor({ state: 'detached', timeout: 5000 })
  check((await toggle.getAttribute('aria-pressed')) === 'false', 'toggle reports off')
  await toggle.click()
  await callout.waitFor()
  pass('"Turn off Agent Mode" removes the callout and flips the header toggle; toggling on restores it')

  // 5. Reduce helper text.
  await seed(page, base, { tools: [], settings: { reduceHelpText: true } })
  await callout.waitFor()
  check((await page.getByRole('button', { name: 'What Agent Mode does' }).count()) === 0, 'help button hidden')
  check(await callout.getByText('Select at least one tool to let the assistant take multiple steps.').isVisible(), 'short copy')
  check((await callout.getByText(/inspects the results/).count()) === 0, 'long explanation hidden')
  check(await callout.getByRole('button', { name: 'Choose tools' }).isVisible(), 'Choose tools kept')
  check(await callout.getByRole('button', { name: 'Turn off Agent Mode' }).isVisible(), 'Turn off kept')
  await shot(page, '5-reduce-helper-text')
  pass('Reduce helper text hides the help button and long copy but keeps the state and both actions')

  // 6. Running agent: footer status, step count, visible when scrolled up, Stop works.
  await seed(page, base, { tools: ['atlas_sleep'] })
  check((await callout.count()) === 0, 'no callout once a tool is selected')
  const textbox2 = page.getByRole('textbox', { name: /Type a message/ })
  // A first quick exchange gives the transcript something to scroll.
  await textbox2.fill('words=400 stream_ms=0 steps=0 label=PRE')
  await page.getByTestId('send-button').click()
  await page.getByText('END[PRE]').waitFor({ timeout: 30000 })
  await textbox2.fill('sleep=6 steps=4 label=RUN1088')
  await page.getByTestId('send-button').click()
  const status = page.getByTestId('agent-run-status')
  await status.waitFor({ timeout: 15000 })
  check(await status.getByText('Agent is running').isVisible(), 'running title')
  await status.getByText(/step \d+/).waitFor({ timeout: 20000 })
  // Scroll the way a person does (wheel), so the transcript's own
  // follow-the-bottom logic sees it as a user scroll and lets go.
  const transcript = page.locator('.chat-messages')
  const tbox = await transcript.boundingBox()
  await page.mouse.move(tbox.x + tbox.width / 2, tbox.y + tbox.height / 2)
  for (let i = 0; i < 20 && (await transcript.evaluate(el => el.scrollTop)) > 0; i++) {
    await page.mouse.wheel(0, -3000)
    await page.waitForTimeout(150)
  }
  await page.waitForTimeout(500)
  check((await transcript.evaluate(el => el.scrollTop)) === 0, 'transcript scrolled to the top')
  check(await inViewport(page.getByText('PRE-w400 END[PRE]')) === false, 'newest output is off screen')
  check(await inViewport(status), 'run status visible with transcript scrolled to the top')
  await shot(page, '6-agent-running-scrolled-up')
  pass('Agent run status shows "Agent is running" with a step count and stays visible when scrolled to the top')

  await status.getByRole('button', { name: 'Stop agent run' }).click()
  await status.waitFor({ state: 'detached', timeout: 20000 })
  check((await page.getByText('END[RUN1088]').count()) === 0, 'stopped run did not finish its answer')
  pass('Stop in the run status ends the run and the status row goes away')

  // 7. Tools disabled for the deployment.
  const nt = `http://127.0.0.1:${NO_TOOLS_PORT}`
  await seed(page, nt, { tools: [] })
  await callout.waitFor()
  check((await callout.getByRole('button', { name: 'Choose tools' }).count()) === 0, 'no Choose tools when tools disabled')
  check(await callout.getByText('Tools are disabled for this deployment.').isVisible(), 'disabled note in callout')
  check(await callout.getByRole('button', { name: 'Turn off Agent Mode' }).isVisible(), 'Turn off kept when tools disabled')
  await page.getByRole('button', { name: 'What Agent Mode does' }).click()
  const ntDialog = page.getByRole('dialog', { name: 'What Agent Mode does' })
  await ntDialog.waitFor()
  check((await ntDialog.getByRole('button', { name: 'Choose tools' }).count()) === 0, 'no Choose tools in help')
  check(await ntDialog.getByText('Tools are disabled for this deployment.').isVisible(), 'disabled note in help')
  await shot(page, '7-tools-disabled')
  pass('With tools disabled, callout and help say so and offer no Choose tools')

  await browser.close()
  console.log('ALL PASSED')
} catch (err) {
  try { if (page && SHOTS) await page.screenshot({ path: join(SHOTS, 'debug-failure.png') }) } catch { /* best effort */ }
  console.error(err)
  process.exitCode = 1
} finally {
  for (const p of procs) p.kill()
}
