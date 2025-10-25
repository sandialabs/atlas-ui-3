import { test, expect } from '@playwright/test';

test.describe('MCP Marketplace E2E Tests', () => {
  
  test.beforeEach(async ({ page }) => {
    // Navigate to the Chat UI application
    await page.goto('/');
    
    // Wait for the page to load and websocket to connect
    await page.waitForSelector('h1:has-text("Chat UI")');
    await page.waitForTimeout(2000); // Give websocket time to connect
  });

  test('should load the main page and display Chat UI', async ({ page }) => {
    // Verify the page title
    await expect(page).toHaveTitle('Chat UI');
    
    // Verify main heading is present
    await expect(page.locator('h1:has-text("Chat UI")')).toBeVisible();
    
    // Verify welcome message is displayed
    await expect(page.locator('text=Select a model and start chatting')).toBeVisible();
  });

  test('should be able to toggle tools panel', async ({ page }) => {
    // Resize browser for consistent testing
    await page.setViewportSize({ width: 1400, height: 1000 });
    
    // Click the Toggle Tools button to open the tools panel
    await page.getByRole('button', { name: 'Toggle Tools' }).click();
    
    // Verify the Tools & Integrations panel is visible
    await expect(page.locator('h2:has-text("Tools & Integrations")')).toBeVisible();
    
    // Verify the Marketplace button is visible
    await expect(page.getByRole('button', { name: 'Marketplace', exact: true })).toBeVisible();
    
    // Verify the "Go to Marketplace" button is visible
    await expect(page.getByRole('button', { name: 'Go to Marketplace' })).toBeVisible();
  });

  test('should navigate to marketplace and display MCP server cards', async ({ page }) => {
    // Resize browser for consistent testing
    await page.setViewportSize({ width: 1400, height: 1000 });
    
    // Open the tools panel
    await page.getByRole('button', { name: 'Toggle Tools' }).click();
    
    // Navigate to marketplace
    await page.getByRole('button', { name: 'Marketplace', exact: true }).click();
    
    // Verify we're on the marketplace page
    await expect(page).toHaveURL('/marketplace');
    await expect(page.locator('h1:has-text("MCP Marketplace")')).toBeVisible();
    
    // Verify marketplace description
    await expect(page.locator('text=Select which MCP servers to use in your chat interface')).toBeVisible();
    
    // Verify server counter is displayed
    await expect(page.locator('text=of 7 servers selected')).toBeVisible();
  });

  test('should display MCP server cards with enhanced metadata', async ({ page }) => {
    // Resize browser for consistent testing
    await page.setViewportSize({ width: 1400, height: 1000 });
    
    // Navigate to marketplace
    await page.getByRole('button', { name: 'Toggle Tools' }).click();
    await page.getByRole('button', { name: 'Marketplace', exact: true }).click();
    
    // Take screenshot of the marketplace
    await page.screenshot({ 
      path: 'screenshots/marketplace-overview.png', 
      fullPage: true 
    });
    
    // Verify specific MCP server cards are displayed with short descriptions
    await expect(page.locator('h3:has-text("calculator")')).toBeVisible();
    await expect(page.locator('text=Basic mathematical calculations')).toBeVisible();
    
    await expect(page.locator('h3:has-text("thinking")')).toBeVisible();
    await expect(page.locator('text=Structured thinking and problem analysis tool')).toBeVisible();
    
    await expect(page.locator('h3:has-text("pdfbasic")')).toBeVisible();
    await expect(page.locator('text=PDF analysis tool')).toBeVisible();
    
    await expect(page.locator('h3:has-text("ui-demo")')).toBeVisible();
    await expect(page.locator('text=Demo server showcasing custom UI modification capabilities')).toBeVisible();
    
    await expect(page.locator('h3:has-text("code-executor")')).toBeVisible();
    await expect(page.locator('text=Secure code execution environment')).toBeVisible();
    
    await expect(page.locator('h3:has-text("prompts")')).toBeVisible();
    await expect(page.locator('text=Specialized system prompts for AI behavior modification')).toBeVisible();
    
    await expect(page.locator('h3:has-text("canvas")')).toBeVisible();
    await expect(page.locator('text=Canvas for showing final rendered content')).toBeVisible();
    
    // Verify enhanced metadata fields are displayed
    await expect(page.locator('text=By: Chat UI Team').first()).toBeVisible();
    await expect(page.locator('text=Get Help').first()).toBeVisible();
  });

  // test('should be able to select and deselect MCP servers', async ({ page }) => {
  //   // Resize browser for consistent testing
  //   await page.setViewportSize({ width: 1400, height: 1000 });
    
  //   // Navigate to marketplace
  //   await page.getByRole('button', { name: 'Toggle Tools' }).click();
  //   await page.getByRole('button', { name: 'Marketplace', exact: true }).click();
    
  //   // Take screenshot of initial state
  //   await page.screenshot({ 
  //     path: 'screenshots/marketplace-initial-state.png', 
  //     fullPage: true 
  //   });
    
  //   // Verify initial state shows 0 servers selected
  //   await expect(page.locator('text=0 of 7 servers selected')).toBeVisible();
    
  //   // Click on calculator server card to select it
  //   await page.locator('text=calculatorPerform basic math operationsBasic mathematical calculations').click();
    
  //   // Take screenshot after selection
  //   await page.screenshot({ 
  //     path: 'screenshots/marketplace-server-selected.png', 
  //     fullPage: true 
  //   });
    
  //   // Verify 1 server is now selected
  //   await expect(page.locator('text=1 of 7 servers selected')).toBeVisible();
    
  //   // Verify the calculator card shows it's selected (has checkmark icon)
  //   await expect(page.locator('[data-testid="calculator-selected"]').or(page.locator('img').first())).toBeVisible();
  // });

  // test('should display control buttons in marketplace', async ({ page }) => {
  //   // Resize browser for consistent testing
  //   await page.setViewportSize({ width: 1400, height: 1000 });
    
  //   // Navigate to marketplace
  //   await page.getByRole('button', { name: 'Toggle Tools' }).click();
  //   await page.getByRole('button', { name: 'Marketplace', exact: true }).click();
    
  //   // Take screenshot of control buttons
  //   await page.screenshot({ 
  //     path: 'screenshots/marketplace-controls.png', 
  //     fullPage: false 
  //   });
    
  //   // Verify control buttons are present (Select All button was removed from marketplace)
  //   await expect(page.getByRole('button', { name: 'Deselect All' })).toBeVisible();
  //   await expect(page.getByRole('button', { name: 'Back to Chat' })).toBeVisible();
    
  //   // Verify that the marketplace controls area only has Deselect All button
  //   // (We check the controls container specifically, not the entire page)
  //   const controlsSection = page.locator('.flex.gap-4.mb-6').first();
  //   await expect(controlsSection.getByRole('button', { name: 'Deselect All' })).toBeVisible();
  //   await expect(controlsSection.getByRole('button', { name: 'Select All' })).not.toBeVisible();
  // });

  // test('should display help information in marketplace', async ({ page }) => {
  //   // Resize browser for consistent testing  
  //   await page.setViewportSize({ width: 1400, height: 1000 });
    
  //   // Navigate to marketplace
  //   await page.getByRole('button', { name: 'Toggle Tools' }).click();
  //   await page.getByRole('button', { name: 'Marketplace', exact: true }).click();
    
  //   // Verify help section is displayed
  //   await expect(page.locator('h4:has-text("How it works:")')).toBeVisible();
    
  //   // Verify help content
  //   await expect(page.locator('text=Select the MCP servers you want to use in your chat interface')).toBeVisible();
  //   await expect(page.locator('text=Only selected servers will appear in the Tools & Integrations panel')).toBeVisible();
  //   await expect(page.locator('text=Purple tags')).toBeVisible();
  //   await expect(page.locator('text=indicate custom prompts')).toBeVisible();
  //   await expect(page.locator('text=gray tags')).toBeVisible();
  //   await expect(page.locator('text=indicate tools')).toBeVisible();
  // });
});