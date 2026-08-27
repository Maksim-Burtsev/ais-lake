import { expect, test } from '@playwright/test';

const SHOTS = 'test-results';

for (const theme of ['night', 'day'] as const) {
  test(`map shell · ${theme}`, async ({ page }) => {
    await page.goto(`/?theme=${theme}`);
    await page.waitForFunction(() => document.fonts.status === 'loaded');
    // freeze the LIVE dot / sweep so shots are comparable between runs
    await page.addStyleTag({ content: '*, *::before, *::after { animation: none !important }' });
    await page.locator('header').waitFor();
    // the map must actually be alive: a dead worker or a collapsed container
    // both leave a blank sea that a screenshot-only spec would happily pass
    const canvas = page.locator('.maplibregl-canvas');
    await expect(canvas).toBeVisible();
    await expect
      .poll(async () => (await canvas.boundingBox())?.height ?? 0, { timeout: 15_000 })
      .toBeGreaterThan(300);
    // let the basemap finish: tiles fetched and the map gone idle
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${SHOTS}/shell-${theme}-1440x900.png` });
  });
}
