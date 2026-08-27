import { test } from '@playwright/test';

const SHOTS = 'test-results';

for (const theme of ['night', 'day'] as const) {
  test(`map shell · ${theme}`, async ({ page }) => {
    await page.goto(`/?theme=${theme}`);
    await page.waitForFunction(() => document.fonts.status === 'loaded');
    // freeze the LIVE dot / sweep so shots are comparable between runs
    await page.addStyleTag({ content: '*, *::before, *::after { animation: none !important }' });
    await page.locator('header').waitFor();
    await page.screenshot({ path: `${SHOTS}/shell-${theme}-1440x900.png` });
  });
}
