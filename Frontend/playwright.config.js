import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  testIgnore: '**/aws/**',
  fullyParallel: true,
  workers: 2,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:5183',
    channel: 'msedge',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm.cmd run dev -- --port 5183',
    env: { VITE_DATA_MODE: 'demo' },
    url: 'http://127.0.0.1:5183',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
