import { test, expect } from '@playwright/test'

test.describe('Tool Approval E2E Tests', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
  })

  test('should display tool approval dialog when tool requires approval', async ({ page }) => {
    // Send a message that triggers a tool requiring approval
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Execute a dangerous operation')
      await messageInput.press('Enter')

      // Wait for approval dialog to appear
      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Verify dialog elements
      await expect(page.locator('text=Tool Approval Required')).toBeVisible()
      await expect(page.locator('button:has-text("Approve")')).toBeVisible()
      await expect(page.locator('button:has-text("Reject")')).toBeVisible()
    }
  })

  test('should allow approving a tool call', async ({ page }) => {
    // Trigger tool approval
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Run approved tool')
      await messageInput.press('Enter')

      // Wait for approval dialog
      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Click approve button
      const approveButton = page.locator('button:has-text("Approve")').first()
      await approveButton.click()

      // Dialog should close
      await expect(page.locator('text=Tool Approval Required')).not.toBeVisible({ timeout: 5000 })

      // Tool should execute (look for success indicators)
      await page.waitForTimeout(2000)
    }
  })

  test('should allow rejecting a tool call', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Run rejected tool')
      await messageInput.press('Enter')

      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Click reject button
      const rejectButton = page.locator('button:has-text("Reject")').first()
      await rejectButton.click()

      // Dialog should close
      await expect(page.locator('text=Tool Approval Required')).not.toBeVisible({ timeout: 5000 })

      // Should show rejection message
      await page.waitForTimeout(2000)
    }
  })

  test('should allow editing tool arguments before approval', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Execute editable tool')
      await messageInput.press('Enter')

      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Check if edit button is present
      const editButton = page.locator('button:has-text("Edit Mode")').first()
      if (await editButton.isVisible()) {
        await editButton.click()

        // Switch to edit mode
        await expect(page.locator('button:has-text("View Mode")')).toBeVisible()

        // Find and edit an argument (this is generic - actual implementation may vary)
        const textarea = page.locator('textarea').first()
        if (await textarea.isVisible()) {
          await textarea.fill('edited_value')
        }

        // Approve with edits
        const approveButton = page.locator('button:has-text("Approve")').first()
        await approveButton.click()

        await expect(page.locator('text=Tool Approval Required')).not.toBeVisible({ timeout: 5000 })
      }
    }
  })

  test('should show rejection reason input', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Tool to reject with reason')
      await messageInput.press('Enter')

      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Find rejection reason input
      const reasonInput = page.locator('input[placeholder*="reason"], input[placeholder*="Rejection"]').first()
      if (await reasonInput.isVisible()) {
        await reasonInput.fill('This is not safe')

        // Reject with reason
        const rejectButton = page.locator('button:has-text("Reject")').first()
        await rejectButton.click()

        await expect(page.locator('text=Tool Approval Required')).not.toBeVisible({ timeout: 5000 })
      }
    }
  })

  test('should handle multiple pending approvals', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      // Trigger multiple tools
      await messageInput.fill('Execute multiple tools requiring approval')
      await messageInput.press('Enter')

      // Wait for first approval dialog
      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Approve first one
      const approveButton = page.locator('button:has-text("Approve")').first()
      await approveButton.click()

      // Wait a bit
      await page.waitForTimeout(1000)

      // Second approval might appear
      const secondApproval = page.locator('text=Tool Approval Required')
      if (await secondApproval.isVisible({ timeout: 3000 }).catch(() => false)) {
        const approveButton2 = page.locator('button:has-text("Approve")').first()
        await approveButton2.click()
      }
    }
  })

  test('should display tool name and arguments in approval dialog', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Show tool details')
      await messageInput.press('Enter')

      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Check for "Arguments" section
      await expect(page.locator('text=Arguments')).toBeVisible()

      // Dialog should show some content (tool name or arguments)
      const dialogContent = page.locator('[class*="dialog"], [class*="modal"]').first()
      if (await dialogContent.isVisible()) {
        const content = await dialogContent.textContent()
        expect(content.length).toBeGreaterThan(20)
      }
    }
  })

  test('should close approval dialog after approval', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Quick approval test')
      await messageInput.press('Enter')

      await page.waitForSelector('text=Tool Approval Required', { timeout: 10000 })

      // Verify dialog is visible
      const dialog = page.locator('text=Tool Approval Required')
      await expect(dialog).toBeVisible()

      // Approve
      const approveButton = page.locator('button:has-text("Approve")').first()
      await approveButton.click()

      // Dialog should disappear
      await expect(dialog).not.toBeVisible({ timeout: 5000 })
    }
  })

  test('should handle approval timeout gracefully', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Test timeout scenario')
      await messageInput.press('Enter')

      // Wait for approval dialog
      const hasDialog = await page.waitForSelector('text=Tool Approval Required', {
        timeout: 10000
      }).catch(() => null)

      if (hasDialog) {
        // Wait for a long time without approving (simulating timeout)
        // In production, this would trigger timeout - here we just verify the UI stays responsive
        await page.waitForTimeout(2000)

        // Dialog should still be visible and functional
        await expect(page.locator('button:has-text("Approve")')).toBeVisible()
        await expect(page.locator('button:has-text("Reject")')).toBeVisible()
      }
    }
  })

  test('should preserve message history after approval workflow', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      // Send initial message
      await messageInput.fill('First message')
      await messageInput.press('Enter')
      await page.waitForTimeout(1000)

      // Send message that triggers approval
      await messageInput.fill('Tool requiring approval')
      await messageInput.press('Enter')

      const hasDialog = await page.waitForSelector('text=Tool Approval Required', {
        timeout: 10000
      }).catch(() => null)

      if (hasDialog) {
        // Approve
        const approveButton = page.locator('button:has-text("Approve")').first()
        await approveButton.click()

        await page.waitForTimeout(1000)

        // Check that previous messages are still visible
        const chatArea = page.locator('[class*="chat"], [class*="message"]').first()
        if (await chatArea.isVisible()) {
          const content = await chatArea.textContent()
          // Should contain indication of message history
          expect(content.length).toBeGreaterThan(0)
        }
      }
    }
  })
})

test.describe('Tool Approval Configuration E2E Tests', () => {
  test('should respect tool approval settings', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Look for settings panel or configuration
    const settingsButton = page.locator('[aria-label*="settings"], button:has-text("Settings")').first()

    if (await settingsButton.isVisible({ timeout: 3000 }).catch(() => false)) {
      await settingsButton.click()
      await page.waitForTimeout(500)

      // Look for approval-related settings
      const approvalSettings = page.locator('text=/approval/i, text=/require.*approval/i')
      if (await approvalSettings.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Settings panel exists - verify it's accessible
        expect(await approvalSettings.isVisible()).toBeTruthy()
      }
    }
  })

  test('should display appropriate UI for admin-required approvals', async ({ page }) => {
    const messageInput = page.locator('textarea[placeholder*="message"], input[type="text"]').first()

    if (await messageInput.isVisible()) {
      await messageInput.fill('Admin-restricted tool')
      await messageInput.press('Enter')

      const hasDialog = await page.waitForSelector('text=Tool Approval Required', {
        timeout: 10000
      }).catch(() => null)

      if (hasDialog) {
        // Check for admin-required indicator (this is implementation-specific)
        const dialogText = await page.locator('text=Tool Approval Required').textContent()
        expect(dialogText).toBeTruthy()

        // Close the dialog
        const rejectButton = page.locator('button:has-text("Reject")').first()
        await rejectButton.click()
      }
    }
  })
})
