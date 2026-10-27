import { test, expect } from '@playwright/test';

test('signup verification, manual rules, and ingestion folder controls', async ({ page }) => {
  const rules = [], sources = [];
  let verified = false;
  await page.route('**/api/**', async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let json = {}, status = 200;
    if (path === '/api/auth/register') { status = 201; json = { email_verification_required: true, verification_delivery: 'sent' }; }
    else if (path === '/api/auth/verify-email') {
      expect(req.postDataJSON().code).toBe('123456'); verified = true;
      json = { message: 'Email verified. You can now sign in.' };
    } else if (path === '/api/auth/login') { expect(verified).toBe(true); json = { access_token: 'test-token' }; }
    else if (path === '/api/auth/me') json = { id: 'user-1', email: 'test@example.com' };
    else if (path === '/api/jobs') json = { items: [], total: 0 };
    else if (path === '/api/rule-sets') {
      if (req.method() === 'POST') {
        const data = req.postDataJSON();
        expect(data.rules.numeric_ranges.age).toEqual({ min: 16, max: 100, integer: true });
        expect(data.rules.unique_columns).toEqual(['student_id']);
        const rule = { ...data, id: 'rule-1', created_at: new Date().toISOString() };
        rules.push(rule); json = rule; status = 201;
      } else json = rules;
    } else if (path === '/api/ingestion/sources') {
      if (req.method() === 'POST') {
        expect(req.postDataJSON().rule_set_id).toBe('rule-1');
        const source = { id: 'source-1', ...req.postDataJSON(), enabled: true, uri: 's3://test/incoming/source-1/' };
        sources.push(source); json = source; status = 201;
      } else json = sources;
    } else if (path === '/api/ingestion/sources/source-1') { sources[0].enabled = false; json = { disabled: true }; }
    else throw new Error(`Unexpected route ${path}`);
    await route.fulfill({ status, json });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Connect backend', exact: true }).click();
  await page.getByRole('button', { name: 'Create an account', exact: true }).click();
  await page.getByLabel('Email address').fill('test@example.com');
  await page.getByLabel('Password', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Verify your email.' })).toBeVisible();
  await page.getByLabel('Verification code').fill('123456');
  await page.getByRole('button', { name: 'Verify email', exact: true }).click();
  await page.getByLabel('Password', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).first().click();
  await page.getByRole('link', { name: 'Rule sets', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Describe rules in English' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Create rule set', exact: true }).click();
  await page.getByLabel('Rule-set name').fill('Student rules');
  await page.getByRole('button', { name: 'Save rule set', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Student rules' })).toBeVisible();
  await page.evaluate(() => { location.hash = '/upload'; });
  await page.getByLabel('Folder name').fill('Student imports');
  await page.getByLabel('Folder rule set', { exact: true }).selectOption('rule-1');
  await page.getByRole('button', { name: 'Create ingestion folder' }).click();
  await expect(page.getByText('s3://test/incoming/source-1/')).toBeVisible();
  await page.screenshot({ path: 'test-results/cloud-features.png', fullPage: true });
  await page.getByRole('button', { name: 'Disable folder' }).click();
  await expect(page.getByText('Student imports (disabled)')).toBeVisible();
});
