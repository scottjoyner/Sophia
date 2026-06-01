import { test, expect } from '@playwright/test';

test('offline diagnostics panel renders mocked server status', async ({ page }) => {
  await page.route('**/healthz', async route => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });
  await page.route('**/diagnostics/offline', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        graph_outbox: { pending_total: 0, due: 0, counts: {} },
        capture_idempotency: { counts: { active: 1, expired: 0 }, healthy: true },
        roles: { neo4j: 'durable Sophia memory brain' },
      }),
    });
  });

  await page.goto('/');
  await expect(page.locator('#offlineDiagnosticsPanel')).toBeVisible();
  await expect(page.locator('#offlineDiagnosticsRefreshBtn')).toBeVisible();
  await page.locator('#offlineDiagnosticsRefreshBtn').click();
  await expect(page.locator('#offlineDiagnosticsStatus')).toContainText('Reliability path healthy');
  await expect(page.locator('#offlineDiagnosticsJson')).toContainText('durable Sophia memory brain');
});
