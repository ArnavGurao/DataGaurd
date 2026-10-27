import { defineConfig } from '@playwright/test';

if (!process.env.DATAGUARD_URL?.startsWith('https://')) {
  throw new Error('Set DATAGUARD_URL to the deployed HTTPS website.');
}

export default defineConfig({
  testDir: './tests/aws',
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: process.env.DATAGUARD_URL,
    channel: process.platform === 'win32' ? 'msedge' : 'chromium',
    headless: true,
    screenshot: 'only-on-failure',
    // Authentication traces include tokens and passwords; do not record them.
    trace: 'off',
  },
});
