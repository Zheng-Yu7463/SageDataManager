import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: process.env.SAGE_E2E_BASE_URL ?? 'http://127.0.0.1:8080',
    screenshot: 'only-on-failure',
    launchOptions: process.env.SAGE_E2E_CHROMIUM_PATH ? { executablePath: process.env.SAGE_E2E_CHROMIUM_PATH } : undefined,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-chromium', use: { ...devices['Pixel 5'] } },
  ],
})
