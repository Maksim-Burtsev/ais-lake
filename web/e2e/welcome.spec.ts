import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

/** F11 acceptance, socket and /v1/map/snapshot mocked (filters.spec.ts pattern).
 *
 *  The criterion is "toast never reappears after dismiss (localStorage)", so the
 *  proof has to be a reload, not a click: clicking only shows React unmounted a
 *  node, which would pass even with nothing persisted at all.
 *
 *  Isolation: Playwright hands every test a fresh browser context, so the
 *  dismissal flag starts empty here and cannot leak into the other specs — which
 *  is why they all still see the toast and none of them has to clear it.
 *
 *  The snapshot IS mocked empty, unlike in filters.spec.ts: that spec only has to
 *  find its own ships among a possibly-seeded dev api's, while the count here is
 *  an exact number and a real fleet sharing the viewport would move it.
 */

const BASE_MMSI = 999000000; // outside the valid range: cannot collide with real data
const LON = 3.0;
const LAT = 55.0;
const COPY = 'Real ships, live. Click any of them.';

/** A fleet of `n` ships scattered well inside the z=10 viewport (~2° x 0.7°). */
const fleet = (n: number, from = 0) =>
  JSON.stringify({
    ts: 1,
    interval: 30,
    vessels: Array.from({ length: n }, (_, k) => {
      const i = from + k;
      return [BASE_MMSI + i, LAT + (i % 37) * 0.001, LON + (i % 41) * 0.001, 45, 9, 'underway', 'tanker3'];
    }),
  });

interface MapHandle {
  getLayer(id: string): unknown;
}

const toast = (page: Page) => page.getByRole('status');
const key = (page: Page) => page.getByRole('group', { name: 'Map key' });

async function boot(page: Page) {
  const live: { socket: WebSocketRoute | null } = { socket: null };
  await page.route('**/v1/map/snapshot*', (route) => route.fulfill({ json: { vessels: [] } }));
  // React StrictMode mounts the map twice in dev: the surviving client is the last.
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
  });
  await page.goto(`/?theme=night&z=10&c=${LON.toFixed(4)},${LAT.toFixed(4)}`);
  await expect
    .poll(
      () =>
        page.evaluate(() =>
          Boolean((window as unknown as { __map?: MapHandle }).__map?.getLayer('vessels-hull')),
        ),
      { timeout: 30_000 },
    )
    .toBe(true);
  return live;
}

test('welcome · the toast greets a first visit, and the ✕ retires it for good', async ({ page }) => {
  await boot(page);

  await expect(toast(page)).toHaveText(new RegExp(`^${COPY}`));
  await toast(page).getByRole('button', { name: 'Dismiss' }).click();
  await expect(toast(page)).toBeHidden();

  // the criterion: a reload must not bring it back.
  await page.reload();
  await expect(key(page)).toBeVisible(); // the shell really did come back up
  await expect(toast(page)).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('welcome-dismissed'))).toBe('1');

  // ... and neither does a fresh navigation in the same browser.
  await page.goto('/?theme=day');
  await expect(key(page)).toBeVisible();
  await expect(toast(page)).toBeHidden();
});

test('welcome · the map key names three states, in both themes', async ({ page }) => {
  await boot(page);

  const rows = ['Under way', 'At anchor', 'Silent'];
  for (const row of rows) await expect(key(page).getByText(row)).toBeVisible();
  // exactly three: "moored" and "selected" are deliberately not in the frame's key
  await expect(key(page).locator('svg')).toHaveCount(rows.length);

  const fill = () =>
    key(page).evaluate((el) => getComputedStyle(el).backgroundColor);
  const night = await fill();
  await page.getByRole('button', { name: /Switch to daylight/ }).click();
  await expect(page).toHaveURL(/[?&]theme=day/);
  for (const row of rows) await expect(key(page).getByText(row)).toBeVisible();
  expect(await fill()).not.toBe(night); // the panel is tokened, not hard-coded night
});

test('welcome · the count is the fleet in view, comma-grouped, and it tracks', async ({ page }) => {
  const live = await boot(page);
  const count = key(page).getByText(/SHIPS$/);
  await expect(count).toHaveText('0 SHIPS'); // nothing on the water yet

  // frames are re-sent while polling: the first may land before the basemap does.
  await expect
    .poll(
      async () => {
        live.socket?.send(fleet(1234));
        return count.textContent();
      },
      { timeout: 30_000 },
    )
    .toBe('1,234 SHIPS');

  live.socket?.send(fleet(1, 1234)); // one more ship, one more in the key
  await expect(count).toHaveText('1,235 SHIPS');
});
