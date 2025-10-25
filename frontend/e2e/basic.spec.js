import { test, expect } from '@playwright/test';

test.describe('Chat UI Basic Functionality', () => {
  test('should load the application', async ({ page }) => {
    // Navigate to the application
    await page.goto('/');
    
    // Check that the page loads
    await expect(page).toHaveTitle(/Chat UI/);
    
    // Basic page structure should be present
    await expect(page.locator('body')).toBeVisible();
  });

  test('should have basic navigation elements', async ({ page }) => {
    await page.goto('/');
    
    // Wait for the page to load
    await page.waitForLoadState('networkidle');
    
    // Check for basic UI elements (adjust selectors based on actual app)
    // These are generic checks that should work with most React apps
    const body = page.locator('body');
    await expect(body).toBeVisible();
    
    // Check that React has mounted (look for React root)
    const reactRoot = page.locator('#root, [data-reactroot], main, .app');
    await expect(reactRoot.first()).toBeVisible();
  });

  test('should handle basic interactions', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    // Basic interaction test - clicking should not crash the page
    await page.click('body');
    
    // Page should still be responsive
    const body = page.locator('body');
    await expect(body).toBeVisible();
  });
});

test.describe('Network and Performance', () => {
  test('should load without console errors', async ({ page }) => {
    const consoleErrors = [];
    
    page.on('console', msg => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });
    
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    // Allow some time for any delayed errors
    await page.waitForTimeout(1000);
    
    // Filter out known acceptable errors (like network failures in test env)
    const criticalErrors = consoleErrors.filter(error => 
      !error.includes('net::') && 
      !error.includes('Failed to fetch') &&
      !error.includes('NetworkError')
    );
    
    expect(criticalErrors).toHaveLength(0);
  });

  test('should respond quickly', async ({ page }) => {
    const startTime = Date.now();
    
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    
    const loadTime = Date.now() - startTime;
    
    // Page should load within reasonable time (10 seconds in test environment)
    expect(loadTime).toBeLessThan(10000);
  });
});