import { test, expect, request } from '@playwright/test';

const MOCK_LLM_URL = 'http://127.0.0.1:8002';

// Run serially since tests share the same mock LLM server state
test.describe.serial('Context Window Exceeded Error E2E', () => {
  test.setTimeout(60000);

  test.beforeEach(async ({ page }) => {
    // Clear any forced errors from previous tests
    const apiContext = await request.newContext();
    await apiContext.post(`${MOCK_LLM_URL}/test/force-error`, {
      data: { error_type: null },
    });
    await apiContext.dispose();

    await page.goto('/');
    await page.setViewportSize({ width: 1400, height: 1000 });
    await page.waitForSelector('text=Connected', { timeout: 10000 });

    // Select mock-model from the dropdown
    const modelNames = ['openrouter-gpt-4o', 'gpt-4.1-nano', 'gpt-4.1', 'groq-gpt-oss-120b', 'mock-model'];
    for (const name of modelNames) {
      const btn = page.getByRole('button', { name, exact: true }).first();
      if (await btn.isVisible({ timeout: 500 }).catch(() => false)) {
        await btn.click();
        break;
      }
    }
    await page.getByRole('button', { name: 'mock-model', exact: true }).click({ timeout: 5000 });
    await page.waitForTimeout(500);
  });

  test('should display context window exceeded error in chat', async ({ page }) => {
    // Start a new chat
    await page.getByRole('button', { name: 'New Chat' }).click();
    await page.waitForTimeout(500);

    // Force the mock LLM to return a context window exceeded error
    const apiContext = await request.newContext();
    await apiContext.post(`${MOCK_LLM_URL}/test/force-error`, {
      data: { error_type: 'context_window_exceeded', count: 1 },
    });
    await apiContext.dispose();

    // Send a message (avoid the word "error" to not trigger mock's error keyword response)
    const chatInput = page.getByRole('textbox', { name: /Type a message/ });
    await chatInput.fill('tell me about the weather');
    await chatInput.press('Enter');

    // Wait for the context window error message to appear in the chat
    await expect(
      page.getByText('too long for this model\'s context window')
    ).toBeVisible({ timeout: 15000 });

    // Verify the error message contains actionable guidance
    await expect(
      page.getByText('start a new conversation or switch to a model')
    ).toBeVisible();

    // Take screenshot for documentation
    await page.screenshot({
      path: 'screenshots/context-window-error.png',
      fullPage: false,
    });
  });

  test('should recover after context window error', async ({ page }) => {
    // Start a new chat
    await page.getByRole('button', { name: 'New Chat' }).click();
    await page.waitForTimeout(500);

    // Force one context window error
    const apiContext = await request.newContext();
    await apiContext.post(`${MOCK_LLM_URL}/test/force-error`, {
      data: { error_type: 'context_window_exceeded', count: 1 },
    });
    await apiContext.dispose();

    // Send message that triggers the forced error
    const chatInput = page.getByRole('textbox', { name: /Type a message/ });
    await chatInput.fill('this will fail');
    await chatInput.press('Enter');

    // Wait for the context window error
    await expect(
      page.getByText('too long for this model\'s context window')
    ).toBeVisible({ timeout: 15000 });

    // Start a new chat (as the error message suggests)
    await page.getByRole('button', { name: 'New Chat' }).click();
    await page.waitForTimeout(500);

    // Send another message — should succeed now (forced error was count=1, already consumed)
    await chatInput.fill('hello');
    await chatInput.press('Enter');

    // Should get a successful response from mock LLM (scope to main chat area, not sidebar)
    await expect(page.locator('main').getByText('mock LLM assistant')).toBeVisible({ timeout: 15000 });
  });
});
