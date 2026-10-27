import { test, expect } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

test.skip(!process.env.DATAGUARD_URL, 'Run with playwright.aws.config.js against AWS.');

test.afterEach(async ({ page }) => {
  // Playwright's failure context includes input values. Clear generated test
  // passwords before it captures the page, even though these are synthetic users.
  for (const input of await page.locator('input[type="password"]').all()) {
    await input.fill('').catch(() => {});
  }
});

test('live verified login, rules, queued validation, S3 download, and persistent history', async ({
  page,
}) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const email = process.env.DATAGUARD_TEST_EMAIL;
  const password = process.env.DATAGUARD_TEST_PASSWORD;
  test.skip(!email || !password, 'Provide a verified synthetic test account through DATAGUARD_TEST_EMAIL and DATAGUARD_TEST_PASSWORD.');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome back.' })).toBeVisible();
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
  await page.getByRole('link', { name: 'Rule sets', exact: true }).click();
  await page.getByRole('button', { name: 'Create rule set', exact: true }).click();
  await page.getByRole('textbox', { name: 'Rule-set name' }).fill('AWS browser verification');
  await page.getByRole('button', { name: 'Save rule set', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'AWS browser verification', exact: true }),
  ).toBeVisible();
  await page.evaluate(() => {
    window.location.hash = '/upload';
  });
  const sample = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../../samples/students_dirty.csv',
  );
  await page.getByLabel('Choose CSV file').setInputFiles(sample);
  await page.getByLabel('Dataset title').fill('AWS browser CSV audit');
  await page.locator('.upload-form').getByRole('combobox').selectOption({ label: 'AWS browser verification' });
  await page.getByRole('button', { name: 'Validate dataset', exact: true }).click();
  await expect(page.locator('.score-ring strong')).toHaveText('33.3%', { timeout: 90000 });
  await expect(page.locator('.report-counts strong')).toHaveText(['6', '2', '4']);
  const reportHash = new URL(page.url()).hash;
  await page.getByRole('tab', { name: 'Rejected row preview' }).click();
  await expect(page.locator('.preview-table tbody tr')).toHaveCount(4);
  const pendingDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Valid rows', exact: true }).click();
  const download = await pendingDownload;
  await expect(download.failure()).resolves.toBeNull();
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const csv = Buffer.concat(chunks).toString();
  expect(csv).toContain('S001,Asha,20,COMP');
  expect(csv).toContain('S006,Neha,23,IT');
  expect(csv).not.toContain('S003');
  await page.reload();
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
  await page.evaluate((hash) => {
    window.location.hash = hash;
  }, reportHash);
  await expect(page.locator('.report-counts strong')).toHaveText(['6', '2', '4']);
  await page.screenshot({ path: 'test-results/aws-live-report.png', fullPage: true });
  expect(errors).toEqual([]);
});
