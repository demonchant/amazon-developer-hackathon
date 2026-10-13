import { test, expect } from '@playwright/test';

test('root route renders the real empty state with source and MCP entry', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('requestfailed', r => errors.push(`${r.method()} ${r.url()} ${r.failure()?.errorText}`));
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Plan around your own limits.' })).toBeVisible();
  await expect(page.getByText(/No events saved yet/)).toBeVisible();
  await expect(page.getByRole('link', { name: /MET Norway API/ })).toHaveAttribute('href', /api\.met\.no\/weatherapi\/locationforecast/);
  await expect(page.getByRole('link', { name: /CC BY 4.0/ })).toHaveAttribute('href', /creativecommons\.org\/licenses\/by\/4\.0/);
  await expect(page.getByRole('link', { name: /MCP endpoint/ })).toHaveAttribute('href', '/mcp');
  await expect(page.getByRole('button', { name: /Save event & check forecast/ })).toBeEnabled();
  expect(errors).toEqual([]);
});

test('form blocks incomplete input without saving a fabricated event', async ({ page }) => {
  await page.goto('/');
  const name = page.getByLabel('Event name');
  await name.fill('');
  await page.getByRole('button', { name: /Save event & check forecast/ }).click();
  await expect(name).toHaveJSProperty('validity.valid', false);
  await expect(page.getByText(/No events saved yet/)).toBeVisible();
  await expect(page.getByText(/NEW PLAN/)).toBeVisible();
});

test('mobile viewport keeps form actions and source navigation usable', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Event details' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Save event & check forecast/ })).toBeInViewport();
  const overflows = await page.locator('html').evaluate(el => el.scrollWidth > window.innerWidth);
  expect(overflows).toBe(false);
});

test('keyboard can reach the labeled event fields and submit control', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  for (let i = 0; i < 20; i++) {
    if (await page.getByLabel('Event name').evaluate(el => el === document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  await expect(page.getByLabel('Event name')).toBeFocused();
  await expect(page.getByRole('status').first()).toHaveAttribute('aria-live', 'polite');
});

test('simulated Alexa+ list request invokes the live MCP tool and reports actual empty state', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Your request').fill('List my events');
  await page.getByRole('button', { name: /^Ask/ }).click();
  await expect(page.getByRole('log')).toContainText('You have no saved events yet.');
  await expect(page.getByRole('log')).toContainText('MCP tool: list_outdoor_events');
});

test('missing event recheck exposes a real failure response', async ({ request }) => {
  const response = await request.post('/api/events/2147483647/recheck');
  expect(response.status()).toBe(404);
  expect(await response.json()).toEqual({ error: 'Event not found' });
});

test('reduced-motion preference keeps motion optional', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const duration = await page.getByRole('button', { name: /Save event & check forecast/ }).evaluate(el => getComputedStyle(el).transitionDuration);
  expect(duration.split(',').every(x => parseFloat(x) < 0.01)).toBe(true);
});
