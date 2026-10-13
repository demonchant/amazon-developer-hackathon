import { test, expect } from '@playwright/test';

test('landing page explains FieldCall and links into the product', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('requestfailed', r => errors.push(`${r.method()} ${r.url()} ${r.failure()?.errorText}`));
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Make room for the forecast.' })).toBeVisible();
  await expect(page.getByText(/Save the event/)).toBeVisible();
  await expect(page.getByText(/Set your limits/)).toBeVisible();
  await expect(page.getByText(/Check the forecast/)).toBeVisible();
  await expect(page.getByRole('link', { name: /Try FieldCall/ })).toHaveAttribute('href', '/dashboard');
  await expect(page.getByRole('link', { name: /MET Norway Locationforecast/ })).toHaveAttribute('href', /api\.met\.no\/weatherapi\/locationforecast/);
  await expect(page.locator('.hero-art img')).toHaveAttribute('src', '/fieldcall-hero.webp');
  expect(await page.locator('.hero-art img').evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(1000);
  await expect(page.getByText(/not safety advice/)).toBeVisible();
  expect(errors).toEqual([]);
});

test('private dashboard sends a signed-out visitor to Google sign-in', async ({ request }) => {
  const dashboard = await request.get('/dashboard', { maxRedirects: 0 });
  expect(dashboard.status()).toBe(303);
  expect(dashboard.headers().location).toBe('/auth/login');
  const login = await request.get('/auth/login', { maxRedirects: 0 });
  expect(login.status()).toBe(302);
  expect(login.headers().location).toMatch(/^https:\/\/accounts\.google\.com\/o\/oauth2\/v2\/auth\?/);
  expect(login.headers()['set-cookie']).toContain('fieldcall_oauth_state=');
});

test('private event API refuses access without a signed-in session', async ({ request }) => {
  const response = await request.get('/api/events');
  expect(response.status()).toBe(401);
  expect(await response.json()).toMatchObject({ error: 'Sign in with Google to continue' });
});

test('mobile landing page has no horizontal overflow and keeps the main action visible', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('link', { name: /Try FieldCall/ })).toBeInViewport();
  const overflows = await page.locator('html').evaluate(el => el.scrollWidth > window.innerWidth);
  expect(overflows).toBe(false);
});

test('keyboard navigation reaches the product call to action', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Tab');
  for (let i = 0; i < 12; i++) {
    if (await page.getByRole('link', { name: /Try FieldCall/ }).evaluate(el => el === document.activeElement)) break;
    await page.keyboard.press('Tab');
  }
  await expect(page.getByRole('link', { name: /Try FieldCall/ })).toBeFocused();
});

test('reduced-motion preference removes decorative transitions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  const duration = await page.getByRole('link', { name: /Try FieldCall/ }).evaluate(el => getComputedStyle(el).transitionDuration);
  expect(duration.split(',').every(x => parseFloat(x) < 0.01)).toBe(true);
});
