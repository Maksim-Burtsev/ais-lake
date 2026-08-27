import { defineConfig } from '@playwright/test';

/** Pixel loop harness: boots the real Vite dev server, shoots the shell at the
 *  frame size (1440x900). No assertions — the screenshots in test-results/ are
 *  compared to docs/design frames by hand. */
export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results/artifacts',
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
  },
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
