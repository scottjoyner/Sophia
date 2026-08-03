import { test, expect } from '@playwright/test';

test('unified Sophia console exposes the AssistX bridge after login', async ({ page }) => {
  await page.route('**/status', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        llm: { assistant_configured: true, assistant_model: 'auto/fast' },
      }),
    });
  });
  await page.route('**/memory-graph/status', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ password_configured: true }),
    });
  });
  await page.route('**/dispatch/status', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        assistx_reachable: true,
        assistx_webhook_ok: true,
        assistx_url: 'http://auto-assist:8000',
      }),
    });
  });

  await page.goto('/');
  await expect(page.locator('#loginForm')).toBeVisible();
  await page.locator('#pass').fill('ci-password');
  await page.locator('#loginBtn').click();

  await expect(page.locator('#app')).toBeVisible();
  await expect(page.locator('#modelPill')).toContainText('auto/fast');
  await expect(page.locator('#assistxPill')).toContainText('connected');

  await page.locator('.nav-item[data-tab="dispatch"]').first().click();
  await expect(page.locator('.panel[data-panel="dispatch"]')).toBeVisible();
  await expect(page.locator('#dispatchSendBtn')).toBeVisible();
  await expect(page.locator('#autoDispatchToggle')).toBeVisible();
  await expect(page.locator('#executionTrace')).toContainText('No dispatch yet');
  await expect(page.locator('#taskList')).toContainText('Tasks extracted');
});
