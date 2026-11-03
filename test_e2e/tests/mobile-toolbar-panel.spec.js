import { test, expect } from '@playwright/test';

// Phone toolbar panels open upward from the composer. With the on-screen
// keyboard up (a short visual viewport) their top used to slide under the
// header -- first from a 12rem minimum height, then from a fixed guess at the
// chrome height when an admin banner message was showing. The cap now follows
// the measured top of the chat area (--atlas-chat-top).
//
// Runs locally, not in CI: the CI e2e step (test/e2e_tests.sh) runs only
// simple_e2e_test.py. With a server on port 8000:
//   cd test_e2e && npm ci && npx playwright test mobile-toolbar-panel

test.describe('phone toolbar panel stays below the header', () => {
  test.use({ viewport: { width: 360, height: 300 }, hasTouch: true, isMobile: true });

  const openFirstPanel = async (page) => {
    const controls = page.getByRole('group', { name: 'Chat controls' });
    await controls.getByRole('button').first().click();
    const panel = page.locator('[aria-label="Chat controls"] .composer-toolbar-popover').first();
    await expect(panel).toBeVisible();
    return panel;
  };

  const geometry = (page) => page.evaluate(() => {
    const panel = [...document.querySelectorAll('[aria-label="Chat controls"] .composer-toolbar-popover')]
      .find((el) => el.getClientRects().length);
    const header = document.querySelector('header');
    // The cap, not the current content, decides whether a tall panel (a long
    // tools list) can reach the header, so check the highest top the panel
    // could have: its bottom edge less its computed max-height.
    const box = panel.getBoundingClientRect();
    return {
      panelTop: box.top,
      highestTop: box.bottom - parseFloat(getComputedStyle(panel).maxHeight),
      headerBottom: header.getBoundingClientRect().bottom,
    };
  });

  test('panel cap keeps its top at or below the header bottom', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('textarea', { timeout: 15000 });
    await openFirstPanel(page);
    const { panelTop, highestTop, headerBottom } = await geometry(page);
    expect(panelTop).toBeGreaterThanOrEqual(headerBottom);
    expect(highestTop).toBeGreaterThanOrEqual(headerBottom);
  });

  test('panel top follows taller chrome above the header', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('textarea', { timeout: 15000 });
    // Stand in for an admin banner message: a 48px strip above the header.
    await page.evaluate(() => {
      const header = document.querySelector('header');
      const strip = document.createElement('div');
      strip.style.height = '48px';
      strip.style.flexShrink = '0';
      header.parentElement.insertBefore(strip, header);
    });
    // Wait for the hook to republish the chat top below the taller chrome.
    await expect.poll(() => page.evaluate(() => {
      const top = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--atlas-chat-top'));
      return top >= document.querySelector('header').getBoundingClientRect().bottom;
    })).toBe(true);
    await openFirstPanel(page);
    const { panelTop, highestTop, headerBottom } = await geometry(page);
    expect(panelTop).toBeGreaterThanOrEqual(headerBottom);
    expect(highestTop).toBeGreaterThanOrEqual(headerBottom);
  });
});
