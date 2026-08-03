import { test, expect } from '@playwright/test';

test('unified Sophia console exposes the AssistX bridge after login', async ({ page }) => {
  await page.goto('/');

  await expect(page.locator('#loginForm')).toBeVisible();
  await page.locator('#pass').fill('ci-password');
  await page.locator('#loginBtn').click();

  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#modelPill')).toContainText('model:');
  await expect(page.locator('#assistxPill')).toContainText('assistx:');

  await page.locator('.nav-item[data-tab="dispatch"]').first().click();
  await expect(page.locator('.panel[data-panel="dispatch"]')).toBeVisible();
  await expect(page.locator('#dispatchUrl')).toBeVisible();
  await expect(page.locator('#dispatchSendBtn')).toBeVisible();
  await expect(page.locator('#autoDispatchToggle')).toBeVisible();
  await expect(page.locator('#executionTrace')).toContainText('No dispatch yet');
  await expect(page.locator('#taskList')).toContainText('Tasks extracted');
});
