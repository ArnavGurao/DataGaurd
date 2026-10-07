import { test, expect } from '@playwright/test';
import { DIRTY_CSV } from '../src/lib/validation.js';

test('desktop dashboard, filtering, pagination, rules, and downloads', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
  await expect(page.locator('.stat-value').first()).toHaveText('7');
  await page.screenshot({ path: 'test-results/dashboard-desktop.png', fullPage: true });
  await page.getByRole('link', { name: 'Datasets' }).click();
  await expect(page.locator('.jobs-table tbody tr')).toHaveCount(6);
  await page.getByRole('button', { name: 'Next page' }).click();
  await expect(page.locator('.jobs-table tbody tr')).toHaveCount(1);
  await page.getByRole('textbox', { name: 'Search datasets…', exact: true }).fill('Engineering');
  await expect(page.locator('.jobs-table tbody tr')).toHaveCount(1);
  await page.getByRole('button', { name: 'View Engineering admissions', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Engineering admissions' })).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'JSON report', exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('engineering_admissions_report.json');
  await page.getByRole('link', { name: 'Rule sets', exact: true }).click();
  await page.getByRole('button', { name: 'Create rule set', exact: true }).click();
  await page.getByRole('textbox', { name: 'Rule-set name' }).fill('My student audit');
  await page.getByRole('button', { name: 'Save rule set', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'My student audit', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View rules' }).last().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('upload README sample, wait for audit, inspect all errors, download valid rows', async ({
  page,
}) => {
  await page.goto('/#/upload');
  await page.getByRole('button', { name: 'Use sample dataset' }).click();
  await page.getByRole('button', { name: 'Validate dataset', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Student records audit' })).toBeVisible();
  await expect(page.getByText('Your dataset is in line.')).toBeVisible();
  await expect(page.locator('.score-ring strong')).toHaveText('33.3%', { timeout: 15000 });
  await expect(page.locator('.report-counts strong')).toHaveText(['6', '2', '4']);
  await expect(page.locator('.failure-row')).toHaveCount(4);
  await page.getByRole('tab', { name: 'Rejected row preview' }).click();
  await expect(page.locator('.preview-table tbody tr')).toHaveCount(4);
  await expect(page.locator('.preview-table tbody tr').nth(1).locator('.row-error')).toHaveCount(2);
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Valid rows', exact: true }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const csv = Buffer.concat(chunks).toString();
  expect(csv).toContain('S001,Asha,20,COMP');
  expect(csv).toContain('S006,Neha,23,IT');
  expect(csv).not.toContain('S003');
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole('tab', { name: 'Validation results' }).click();
  await page.screenshot({ path: 'test-results/report-desktop.png', fullPage: true });
});

test('malformed uploads fail with recovery and missing columns complete with schema errors', async ({
  page,
}) => {
  await page.goto('/#/upload');
  await page
    .getByLabel('Choose CSV file')
    .setInputFiles({
      name: 'broken.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_id,name,age,department\n"unclosed,Test,20,COMP'),
    });
  await page.getByRole('button', { name: 'Validate dataset', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'This audit couldn’t finish' })).toBeVisible({
    timeout: 15000,
  });
  await expect(page.getByText('MALFORMED_CSV', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Upload a corrected file' }).click();
  await page
    .getByLabel('Choose CSV file')
    .setInputFiles({
      name: 'missing.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('student_id,name,age\nS001,Asha,20'),
    });
  await page.getByRole('button', { name: 'Validate dataset', exact: true }).click();
  await expect(page.getByText('Dataset schema needs attention')).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.report-counts strong')).toHaveText(['1', '0', '1']);
});

test('mobile layout, accessible navigation, file validation and registration screen', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/dashboard-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  await page.getByRole('link', { name: 'Rule sets', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Rules worth keeping' })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
  ).toBeTruthy();
  await page.goto('/#/upload');
  await page
    .getByLabel('Choose CSV file')
    .setInputFiles({
      name: 'bad.xlsx',
      mimeType: 'application/octet-stream',
      buffer: Buffer.from('wrong'),
    });
  await expect(page.getByRole('alert')).toContainText('Choose a .csv file');
  await page
    .getByLabel('Choose CSV file')
    .setInputFiles({ name: 'large.csv', mimeType: 'text/csv', buffer: Buffer.alloc(5242881) });
  await expect(page.getByRole('alert')).toContainText('too large');
  await page.getByRole('button', { name: 'Connect backend', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome back.' })).toBeVisible();
  await page.getByRole('button', { name: 'Create an account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Start with confidence.' })).toBeVisible();
  await page.getByRole('button', { name: 'Explore the demo' }).click();
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
});

test('API authentication, authorization headers and expired session recovery', async ({ page }) => {
  let authenticatedRequests = 0;
  await page.route('**/api/**', async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname;
    let json = {};
    if (path === '/api/auth/login') json = { access_token: 'test-token', token_type: 'bearer' };
    else {
      expect(req.headers().authorization).toBe('Bearer test-token');
      authenticatedRequests++;
      if (path === '/api/auth/me') json = { id: 'user-1', email: 'test@example.com' };
      if (path === '/api/jobs') json = { items: [], total: 0 };
      if (path === '/api/rule-sets') json = [];
    }
    await route.fulfill({ json });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Connect backend', exact: true }).click();
  await page.getByRole('textbox', { name: 'Email address' }).fill('test@example.com');
  await page.getByLabel('Password', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Workspace overview' })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Your first dataset belongs here' }),
  ).toBeVisible();
  expect(authenticatedRequests).toBeGreaterThanOrEqual(3);
  expect(
    await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage)),
  ).not.toContain('test-token');
  await page.route('**/api/jobs/expired', (route) =>
    route.fulfill({
      status: 401,
      json: { error: { code: 'TOKEN_EXPIRED', message: 'Expired token' } },
    }),
  );
  await page.evaluate(() => {
    location.hash = '/jobs/expired';
  });
  await expect(page.getByRole('heading', { name: 'Welcome back.' })).toBeVisible();
  await expect(page.getByRole('status')).toContainText('session has expired');
});
