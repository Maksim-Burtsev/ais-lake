import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';
import { CMETA, STATES as SHIP_STATES } from '../src/map/hulls';

/** F2 acceptance, rung 2 (SYMBOLOGY.md §5), with the socket mocked — no api, no
 *  Redis. The two screenshots ARE the acceptance criteria:
 *    symbology-14px.png      — the eight classes at step 2 (14 px), on the frame's
 *                              own stand. A person must be able to name them, or
 *                              the silhouette gets redrawn.
 *    symbology-14px-400.png  — the same fills at 400%, the frame's companion shot.
 *    symbology-greyproof.png — the five states repainted in color.grey_proof.
 *                              Every state must still read with colour removed.
 *  The assertions only guard what a screenshot cannot: that the layers exist and
 *  that every class x step x variant reached the atlas (a missing image is a
 *  console warning and an invisible ship). */

const SHOTS = 'test-results';
const CLASSES = Object.keys(CMETA);
const STATES = ['underway', ...SHIP_STATES];
const LAYERS = ['vessels-loz', 'vessels-hull', 'vessels-shadow'];
/** Every sprite the atlas owes: the class x step cells CMETA allows, each in the
 *  three wake buckets, the four states, and the bare fill. A key that stops being
 *  generated is an invisible ship, and nothing else in the suite would notice. */
const ATLAS_KEYS = CLASSES.flatMap((cls) => {
  const [lo, hi] = CMETA[cls]!.steps;
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i).flatMap((step) =>
    ['u0', 'u1', 'u2', ...SHIP_STATES, 'plain'].map((v) => `${cls}${step}-${v}`),
  );
});
/** The frame's acceptance stand: flat white on flat navy, nothing else. */
const NAVY = '#0F2A4A';
const WHITE = '#F4F7F9';
const HULL = 'vessels-hull';
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
  getStyle(): { layers: { id: string }[] };
  hasImage(id: string): boolean;
  project(lngLat: [number, number]): { x: number; y: number };
  getCanvas(): HTMLCanvasElement;
  setLayoutProperty(id: string, name: string, value: unknown): void;
  getSource(id: string): { serialize(): { data?: { features: unknown[] } } } | undefined;
  setPaintProperty(id: string, name: string, value: unknown): void;
  triggerRepaint(): void;
}

type Row = [sym: string, state: string, sog: number];

/** A fleet laid out in a row, one ship per entry, at the z10 rung-2 zoom. `cog` is
 *  fixed, not per-ship: the acceptance stand compares silhouettes, so they all have
 *  to point the same way. 90 deg is the frame's own `rotate(90)` — bow to the east. */
const SPACING = 0.055;
const COG = 90;
const fleet = (rows: Row[], spacing: number) =>
  JSON.stringify({
    ts: 1,
    interval: 30,
    vessels: rows.map(([sym, state, sog], i) => [
      BASE_MMSI + i, LAT, LON + i * spacing, COG, sog, state, sym,
    ]),
  });

const shipsOnMap = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    return map?.getSource('vessels')?.serialize().data?.features.length ?? 0;
  });

async function boot(page: Page, spacing = SPACING) {
  const warnings: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'warning' || m.type() === 'error') warnings.push(m.text());
  });
  // React StrictMode mounts the map twice in dev: the surviving client is the last.
  const live: { socket: WebSocketRoute | null } = { socket: null };
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
  });
  const centre = LON + spacing * 3.5; // the row is 8 ships wide; centre on its middle
  await page.goto(`/?theme=night&z=10&c=${centre.toFixed(4)},${LAT.toFixed(4)}`);
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
async function seaFills(
  page: Page,
  live: { socket: WebSocketRoute | null },
  rows: Row[],
  spacing = SPACING,
) {
  await expect
    .poll(
      async () => {
        live.socket?.send(fleet(rows, spacing));
        return shipsOnMap(page);
      },
      { timeout: 30_000 },
    )
    .toBe(rows.length);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(1000);
}

/** The stand of §5, rebuilt as the frame's `buildTest()` prints it: bare hull
 *  fills on flat navy — no wake, no shadow, no basemap, no deck detail (the 17-px
 *  gate drops it at this size anyway). Everything else is a confounder. */
async function acceptanceStand(page: Page, iconSize: number) {
  await page.evaluate(
    ({ navy, white, hull, iconSize }) => {
      const map = (window as unknown as { __map?: MapHandle }).__map;
      if (!map) return 0;
      for (const layer of map.getStyle().layers) {
        if (layer.id === hull) continue;
        if (layer.id === 'background') map.setPaintProperty(layer.id, 'background-color', navy);
        else map.setLayoutProperty(layer.id, 'visibility', 'none');
      }
      map.setLayoutProperty(hull, 'icon-image', ['concat', ['get', 'sym'], '-plain']);
      map.setLayoutProperty(hull, 'icon-size', iconSize);
      map.setPaintProperty(hull, 'icon-color', white); // state colour is not on trial here
      map.triggerRepaint();
      return 0;
    },
    { navy: NAVY, white: WHITE, hull: HULL, iconSize },
  );
  await page.waitForTimeout(500);
  // page coordinates, not canvas ones: `clip` is viewport-relative and the map
  // sits under the shell header.
  return page.evaluate((lat) => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    if (!map) return 0;
    return map.project([2.4, lat]).y + map.getCanvas().getBoundingClientRect().top;
  }, LAT);
}

/** deviceScaleFactor 1, unlike the rest of the suite: "14 px" has to mean 14
 *  pixels in the file a human opens, and it is also the harder of the two — a
 *  retina screen gets twice the device pixels for the same hull. */
test.describe('symbology · acceptance', () => {
  test.use({ deviceScaleFactor: 1 });

  test('the 14-px test, eight classes at rung 2', async ({ page }) => {
    const spacing = 0.1; // ~146 px apart at z10: 8 hulls across, room at 400%
    const { live, warnings } = await boot(page, spacing);
    // step 2 is 14 px, the acceptance size. `pleasure` allows only step 1, so it
    // clamps to 10 px rather than invent a length that class never has (§1).
    await seaFills(page, live, CLASSES.map((c): Row => [`${c}2`, 'moored', 0]), spacing);

    let y = await acceptanceStand(page, 1);
    await page.screenshot({
      path: `${SHOTS}/symbology-14px.png`,
      clip: { x: 0, y: y - 45, width: 1440, height: 90 },
    });
    // ... and the same fills at 400%, so the proportions the small one only implies
    // can be checked by eye. The frame prints both for the same reason.
    y = await acceptanceStand(page, 4);
    await page.screenshot({
      path: `${SHOTS}/symbology-14px-400.png`,
      clip: { x: 0, y: y - 90, width: 1440, height: 180 },
    });

    const missing = await page.evaluate((keys) => {
      const map = (window as unknown as { __map?: MapHandle }).__map;
      return keys.filter((key) => !map?.hasImage(key));
    }, ATLAS_KEYS);
    expect(missing).toEqual([]);
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
