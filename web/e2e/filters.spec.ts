import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

/** F7 acceptance, socket mocked (live.spec.ts / symbology.spec.ts patterns).
 *  A filter does not hide anything — it dims the rest to 22% — so the assertions
 *  read the paint expression and the feature properties it is evaluated against,
 *  never pixels. */

const BASE_MMSI = 999000000; // outside the valid range: cannot collide with real data
const LON = 3.0;
const LAT = 55.0;

type Row = [sym: string, state: string];

const fleet = (rows: Row[]) =>
  JSON.stringify({
    ts: 1,
    interval: 30,
    vessels: rows.map(([sym, state], i) => [
      BASE_MMSI + i, LAT, LON + i * 0.05, 45, state === 'underway' ? 9 : 0, state, sym,
    ]),
  });

interface MapHandle {
  getLayer(id: string): unknown;
  getPaintProperty(id: string, name: string): unknown;
  getSource(id: string): { serialize(): { data?: { features: Feat[] } } } | undefined;
}
interface Feat {
  properties: { mmsi: number; cls: string; state: string };
}

/** Our own ships only: a dev api may be seeded, and the real fleet is welcome to
 *  share the viewport — the test just has to be able to find its own. */
const oursOnMap = (page: Page) =>
  page.evaluate((base) => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    const feats = map?.getSource('vessels')?.serialize().data?.features ?? [];
    return Object.fromEntries(
      feats.filter((f) => f.properties.mmsi >= base).map((f) => [f.properties.mmsi - base, f.properties.cls]),
    );
  }, BASE_MMSI);

const hullOpacity = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    // after a reload the layer is rebuilt: no layer yet is "not painted yet".
    return map?.getLayer('vessels-hull') ? map.getPaintProperty('vessels-hull', 'icon-opacity') : null;
  });

async function boot(page: Page, query = '') {
  const live: { socket: WebSocketRoute | null } = { socket: null };
  // React StrictMode mounts the map twice in dev: the surviving client is the last.
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
  });
  await page.goto(`/?theme=night&z=8&c=${(LON + 0.05).toFixed(4)},${LAT.toFixed(4)}${query}`);
  await expect
    .poll(
      () =>
        page.evaluate(() => {
          const map = (window as unknown as { __map?: MapHandle }).__map;
          return Boolean(map?.getLayer('vessels-hull'));
        }),
      { timeout: 30_000 },
    )
    .toBe(true);
  return live;
}

/** Frames are re-sent while polling: the first may land before the basemap does. */
async function seaFills(page: Page, live: { socket: WebSocketRoute | null }, rows: Row[]) {
  await expect
    .poll(
      async () => {
        live.socket?.send(fleet(rows));
        return Object.keys(await oursOnMap(page)).length;
      },
      { timeout: 30_000 },
    )
    .toBe(rows.length);
}

const chip = (page: Page, label: string) => page.getByRole('button', { name: new RegExp(label) });

test('filters · a class chip lives in the URL and dims everything else', async ({ page }) => {
  const live = await boot(page);
  await seaFills(page, live, [['tanker3', 'underway'], ['cargo3', 'underway']]);

  await chip(page, 'Tankers').click();
  await expect(page).toHaveURL(/[?&]f=tankers/);
  await expect(chip(page, 'Tankers')).toHaveAttribute('aria-pressed', 'true');

  // dim, not hide: both ships are still on the map, and the paint is the case
  // expression — evaluated against the classes the two features actually carry.
  expect(await hullOpacity(page)).toEqual(['case', ['==', ['get', 'cls'], 'tanker'], 1, 0.22]);
  expect(await oursOnMap(page)).toEqual({ '0': 'tanker', '1': 'cargo' });

  // the chip survives a reload, because the URL is the state.
  await page.reload();
  await expect(chip(page, 'Tankers')).toHaveAttribute('aria-pressed', 'true');
  await expect
    .poll(() => hullOpacity(page), { timeout: 30_000 })
    .toEqual(['case', ['==', ['get', 'cls'], 'tanker'], 1, 0.22]);
});

test('filters · Recently silent brights the silents and counts them', async ({ page }) => {
  const live = await boot(page);
  await seaFills(page, live, [['cargo3', 'underway']]);

  await chip(page, 'Recently silent').click();
  await expect(page).toHaveURL(/[?&]f=silent/);
  const silentChip = chip(page, 'Recently silent');
  await expect(silentChip).toHaveAttribute('aria-pressed', 'true');
  await expect(silentChip).toContainText('✕');
  await expect(silentChip).toHaveCSS('background-color', 'rgb(255, 106, 82)'); // #FF6A52
  expect(await hullOpacity(page)).toEqual(['case', ['==', ['get', 'state'], 'silent'], 1, 0.22]);

  // nothing silent yet: the line says so in a sentence, not a zero.
  await expect(page.getByText('No ships have gone silent here in the last 24 h.')).toBeVisible();

  // one silent ship in view -> singular sentence, and the pulse rings are there.
  await seaFills(page, live, [['cargo3', 'underway'], ['tanker2', 'silent']]);
  await expect(page.getByText('1 ship went silent here in the last 24 h')).toBeVisible();
  const rings = await page.evaluate(() =>
    ['vessels-pulse-a', 'vessels-pulse-b'].map((id) =>
      Boolean((window as unknown as { __map?: MapHandle }).__map?.getLayer(id)),
    ),
  );
  expect(rings).toEqual([true, true]);

  // the ✕ dismisses back to All ships.
  await silentChip.click();
  await expect(page).not.toHaveURL(/[?&]f=/);
  await expect(chip(page, 'All ships')).toHaveAttribute('aria-pressed', 'true');
  expect(await hullOpacity(page)).toBe(1);
});
