import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

/** F8 + F10 acceptance, socket and /v1/ships mocked (filters.spec.ts pattern).
 *  Selection is a paint expression, so the "which ship is selected" assertions read
 *  the layer's icon-image the way the filter spec reads icon-opacity — never pixels. */

const MMSI = 999000001; // outside the valid range: cannot collide with real data
const LON = 3.0;
const LAT = 55.0;

const IDENTITY = {
  imo: 9327545,
  name: 'Northern Star',
  callsign: 'PBQF',
  flag: 'Netherlands',
  class: 'Tanker',
  sym: 'tanker4',
  size_m: 120,
  draught_m: 8.4,
  destination: 'ROTTERDAM',
  eta: '08-29 06:00',
};
const SENTENCE = 'Under way at 9.8 kn';

const frame = () =>
  JSON.stringify({
    ts: 1,
    interval: 30,
    vessels: [[MMSI, LAT, LON, 45, 9.8, 'underway', 'tanker4']],
  });

interface MapHandle {
  getLayer(id: string): unknown;
  getLayoutProperty(id: string, name: string): unknown;
  project(lngLat: [number, number]): { x: number; y: number };
  getSource(id: string): { serialize(): { data?: { features: Feat[] } } } | undefined;
}
interface Feat {
  properties: { mmsi: number };
}

const SELECTED = (mmsi: number) => [
  'case',
  ['==', ['get', 'mmsi'], mmsi],
  ['concat', ['get', 'sym'], '-selected'], // class + length step: sprites are per-step
  ['get', 'icon'],
];

const hullIcon = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    // after a theme swap the layer is rebuilt: no layer yet is "not painted yet".
    return map?.getLayer('vessels-hull')
      ? map.getLayoutProperty('vessels-hull', 'icon-image')
      : null;
  });

const onMap = (page: Page) =>
  page.evaluate((mmsi) => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    const feats = map?.getSource('vessels')?.serialize().data?.features ?? [];
    return feats.some((f) => f.properties.mmsi === mmsi);
  }, MMSI);

/** Page coordinates of a lon/lat: the canvas sits under the 56 px bar. */
async function pointFor(page: Page, lon: number, lat: number) {
  const box = await page.locator('canvas.maplibregl-canvas').boundingBox();
  const at = await page.evaluate(
    ([lng, la]) => {
      const p = (window as unknown as { __map?: MapHandle }).__map!.project([lng!, la!]);
      return [p.x, p.y];
    },
    [lon, lat],
  );
  return { x: box!.x + at[0]!, y: box!.y + at[1]!, box: box! };
}

async function boot(page: Page, query = '') {
  const live: { socket: WebSocketRoute | null } = { socket: null };
  const asked: string[] = [];
  await page.route('**/v1/ships/*', (route) => {
    asked.push(route.request().url());
    return route.fulfill({
      json: {
        mmsi: MMSI,
        identity: IDENTITY,
        sentence: SENTENCE,
        // the eyebrow counts from this, so it has to be a real "seconds ago"
        latest: { ts: Math.floor(Date.now() / 1000) - 8, sog: 9.8, cog: 45, state: 'underway' },
      },
    });
  });
  // React StrictMode mounts the map twice in dev: the surviving client is the last.
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
  });
  await page.goto(`/?theme=night&z=10&c=${LON.toFixed(4)},${LAT.toFixed(4)}${query}`);
  await expect
    .poll(
      () =>
        page.evaluate(() =>
          Boolean((window as unknown as { __map?: MapHandle }).__map?.getLayer('vessels-hull')),
        ),
      { timeout: 30_000 },
    )
    .toBe(true);
  return { live, asked };
}

/** Frames are re-sent while polling: the first may land before the basemap does. */
async function seaFills(page: Page, live: { socket: WebSocketRoute | null }) {
  await expect
    .poll(
      async () => {
        live.socket?.send(frame());
        return onMap(page);
      },
      { timeout: 30_000 },
    )
    .toBe(true);
}

const card = (page: Page) => page.getByRole('complementary', { name: 'Selected vessel' });

test('card · tapping a ship opens it, and the tap is in the URL', async ({ page }) => {
  const { live, asked } = await boot(page);
  await seaFills(page, live);

  const ship = await pointFor(page, LON, LAT);
  // symbol placement can lag a frame or two; the click is idempotent, so retry it.
  await expect
    .poll(
      async () => {
        await page.mouse.click(ship.x, ship.y);
        return new URL(page.url()).searchParams.get('sel');
      },
      { timeout: 15_000 },
    )
    .toBe(String(MMSI));

  await expect(card(page)).toBeVisible();
  await expect(card(page).getByText('Northern Star')).toBeVisible();
  await expect(card(page).getByText('Tanker · Netherlands · 120 m')).toBeVisible();
  await expect(card(page).getByText(SENTENCE)).toBeVisible();
  await expect(card(page).getByText('MMSI 999 000 001')).toBeVisible();
  await expect(card(page).getByText('DRAUGHT 8.4 m')).toBeVisible();
  await expect(card(page).getByText(/AIS \d+s AGO/)).toBeVisible();
  expect(asked.some((url) => url.endsWith(`/v1/ships/${MMSI}`))).toBe(true);

  // the halo is a paint expression over the fleet, not a rebuilt feature.
  expect(await hullIcon(page)).toEqual(SELECTED(MMSI));

  // ... and the two controls are drawn but honestly dead (F24 / F12 are not built).
  await expect(card(page).getByRole('button', { name: /Follow the story/ })).toBeDisabled();
  await expect(card(page).getByRole('button', { name: /Follow this ship/ })).toBeDisabled();
});

test('card · a pasted ?sel= URL restores the card and the selected sprite', async ({ page }) => {
  // F10: "paste URL in incognito -> identical view". A cold boot, nothing clicked.
  const { live } = await boot(page, `&sel=${MMSI}`);
  await seaFills(page, live);

  await expect(card(page).getByText('Northern Star')).toBeVisible();
  await expect.poll(() => hullIcon(page), { timeout: 10_000 }).toEqual(SELECTED(MMSI));
});

test('card · a click on bare water closes it, and so does Escape', async ({ page }) => {
  const { live } = await boot(page, `&sel=${MMSI}`);
  await seaFills(page, live);
  await expect(card(page)).toBeVisible();

  const ship = await pointFor(page, LON, LAT);
  await page.mouse.click(ship.box.x + 90, ship.box.y + ship.box.height - 90);
  await expect(card(page)).toBeHidden();
  await expect(page).not.toHaveURL(/[?&]sel=/);
  await expect.poll(() => hullIcon(page)).toEqual(SELECTED(0)); // 0 = nothing selected

  await page.goto(`/?theme=night&z=10&c=${LON.toFixed(4)},${LAT.toFixed(4)}&sel=${MMSI}`);
  await expect(card(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(card(page)).toBeHidden();
});

test('card · it survives the theme swap that rebuilds every layer', async ({ page }) => {
  const { live } = await boot(page, `&sel=${MMSI}`);
  await seaFills(page, live);
  expect(await hullIcon(page)).toEqual(SELECTED(MMSI));

  await page.getByRole('button', { name: /Switch to daylight/ }).click();
  await expect(page).toHaveURL(/[?&]theme=day/);
  await expect(card(page).getByText('Northern Star')).toBeVisible();
  await expect.poll(() => hullIcon(page), { timeout: 30_000 }).toEqual(SELECTED(MMSI));
});

test('card · ?sel=banana is ignored, not a boot failure', async ({ page }) => {
  await boot(page, '&sel=banana');
  await expect(card(page)).toBeHidden();
  await expect(page.getByRole('button', { name: 'All ships' })).toBeVisible();
});
