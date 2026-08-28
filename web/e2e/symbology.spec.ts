import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

/** F2 acceptance, rung 2 (SYMBOLOGY.md §5), with the socket mocked — no api, no
 *  Redis. The two screenshots ARE the acceptance criteria:
 *    symbology-14px.png      — the eight classes at step 2 (14 px). A person must
 *                              be able to name them, or the silhouette gets redrawn.
 *    symbology-greyproof.png — the five states repainted in color.grey_proof.
 *                              Every state must still read with colour removed.
 *  The assertions only guard what a screenshot cannot: that the layers exist and
 *  that every class resolved to a sprite (a missing image is a console warning
 *  and an invisible ship). */

const SHOTS = 'test-results';
const CLASSES = ['tanker', 'cargo', 'ferry', 'fishing', 'tug', 'hsc', 'pleasure', 'unknown'];
const STATES = ['underway', 'anchored', 'moored', 'silent', 'selected'];
const LAYERS = ['vessels-loz', 'vessels-hull', 'vessels-shadow'];
/** tokens.json color.grey_proof — the deuteranopia proof set. */
const GREY = {
  hull: '#EFEFEF',
  anchor: '#D3D3D3',
  moor: '#C0C0C0',
  silent: '#8A8A8A',
  halo: '#CFCFCF',
};

const LON = 2.4;
const LAT = 55.0;
const BASE_MMSI = 999000000; // outside the valid range: cannot collide with real data

interface MapHandle {
  getLayer(id: string): unknown;
  getSource(id: string): { serialize(): { data?: { features: unknown[] } } } | undefined;
  setPaintProperty(id: string, name: string, value: unknown): void;
  triggerRepaint(): void;
}

type Row = [sym: string, state: string, sog: number];

/** A fleet laid out in a row, one ship per entry, at the z10 rung-2 zoom. */
const fleet = (rows: Row[]) =>
  JSON.stringify({
    ts: 1,
    interval: 30,
    vessels: rows.map(([sym, state, sog], i) => [
      BASE_MMSI + i, LAT, LON + i * 0.055, 45, sog, state, sym,
    ]),
  });

const shipsOnMap = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    return map?.getSource('vessels')?.serialize().data?.features.length ?? 0;
  });

async function boot(page: Page) {
  const warnings: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'warning' || m.type() === 'error') warnings.push(m.text());
  });
  // React StrictMode mounts the map twice in dev: the surviving client is the last.
  const live: { socket: WebSocketRoute | null } = { socket: null };
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
  });
  await page.goto(`/?theme=night&z=10&c=${(LON + 0.19).toFixed(4)},${LAT.toFixed(4)}`);
  await page.waitForFunction(() => document.fonts.status === 'loaded');
  await page.addStyleTag({ content: '*, *::before, *::after { animation: none !important }' });
  await expect
    .poll(() => page.evaluate(() => {
      const map = (window as unknown as { __map?: MapHandle }).__map;
      return Boolean(map?.getLayer('vessels-hull'));
    }), {
      timeout: 30_000,
    })
    .toBe(true);
  return { live, warnings };
}

/** Frames are re-sent while polling: the first may land before the basemap does
 *  (the socket does not wait for it) or before the StrictMode remount settles. */
async function seaFills(page: Page, live: { socket: WebSocketRoute | null }, rows: Row[]) {
  await expect
    .poll(
      async () => {
        live.socket?.send(fleet(rows));
        return shipsOnMap(page);
      },
      { timeout: 30_000 },
    )
    .toBe(rows.length);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1000);
}

test('symbology · the 14-px test, eight classes at rung 2', async ({ page }) => {
  const { live, warnings } = await boot(page);
  // step 2 is 14 px — the acceptance size. Under way at mid speed, so wakes show.
  await seaFills(page, live, CLASSES.map((c): Row => [`${c}2`, 'underway', 7]));
  await page.screenshot({ path: `${SHOTS}/symbology-14px.png` });

  const layers = await page.evaluate(
    (ids) => {
      const map = (window as unknown as { __map?: MapHandle }).__map;
      return ids.map((id) => Boolean(map?.getLayer(id)));
    },
    LAYERS,
  );
  expect(layers).toEqual(LAYERS.map(() => true));
  // a class whose sprite never reached the atlas warns and then draws nothing
  expect(warnings.filter((w) => w.toLowerCase().includes('image'))).toEqual([]);
});

test('symbology · every state survives colour removal', async ({ page }) => {
  const { live, warnings } = await boot(page);
  await seaFills(page, live, STATES.map((s): Row => ['cargo3', s, s === 'underway' ? 12 : 0]));

  // Repaint the fleet in the grey proof set: hue is gone, and only the shape cues
  // are left — wake · swing ring · berth line · dashed double ring · double halo.
  await page.evaluate((grey) => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    map?.setPaintProperty('vessels-hull', 'icon-color', [
      'match', ['get', 'state'],
      'anchored', grey.anchor,
      'moored', grey.moor,
      'silent', grey.silent,
      'selected', grey.halo,
      grey.hull,
    ]);
    map?.triggerRepaint();
  }, GREY);
  await page.waitForTimeout(500);

  await page.screenshot({ path: `${SHOTS}/symbology-greyproof.png` });
  expect(warnings.filter((w) => w.toLowerCase().includes('image'))).toEqual([]);
});
