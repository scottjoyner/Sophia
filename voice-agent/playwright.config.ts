import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests-ui',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [['list']],
  use: {
    baseURL: process.env.SOPHIA_UI_BASE_URL || 'http://127.0.0.1:8765',
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 7'] },
    },
  ],
});
