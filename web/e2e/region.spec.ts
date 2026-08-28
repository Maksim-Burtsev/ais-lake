import { expect, test, type Page, type WebSocketRoute } from '@playwright/test';

/** F6 acceptance: "switch re-centres and re-subscribes the WS bbox in <1 s".
 *  /v1/regions is mocked so the counts are known, and the socket is mocked so the
 *  bbox patch can be read off the wire (live.spec.ts pattern). */

const KATTEGAT: [number, number, number, number] = [9.5, 55.5, 13.0, 58.0];

const REGIONS = {
  regions: [
    { slug: 'north-sea', name: 'North Sea', bbox: [-6.5, 49, 13, 61.5], count: 4377 },
    { slug: 'baltic', name: 'Baltic', bbox: [9.5, 53.5, 30, 66], count: null },
  ],
  straits: [
    { slug: 'dover-strait', name: 'Dover Strait', bbox: [0.9, 50.7, 2.2, 51.4], count: 96 },
    { slug: 'kattegat', name: 'Kattegat', bbox: KATTEGAT, count: 212 },
  ],
};

interface MapHandle {
  getCenter(): { lng: number; lat: number };
}
const centre = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    return map ? [map.getCenter().lng, map.getCenter().lat] : null;
  });

const opener = (page: Page) => page.getByRole('button', { expanded: false }).first();

type Vessel = [number, number, number, number, number, string, string];

/** `vessels` is asked per request with the bbox, so a test can hand the Kattegat
 *  a different fleet than the launch view. */
async function boot(page: Page, vessels: (bbox: number[]) => Vessel[] = () => [], query = '') {
  const sent: string[] = [];
  const snapshots: string[] = [];
  const live: { socket: WebSocketRoute | null } = { socket: null };

  await page.route('**/v1/regions', (route) => route.fulfill({ json: REGIONS }));
  await page.route('**/v1/map/snapshot*', (route) => {
    const url = route.request().url();
    snapshots.push(url);
    const box = (new URL(url).searchParams.get('bbox') ?? '').split(',').map(Number);
    const rows = vessels(box);
    return route.fulfill({ json: { region: 'north-sea', ts: 1, count: rows.length, vessels: rows } });
  });
  // React StrictMode mounts twice: the surviving client is the last to connect.
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
    ws.onMessage((message) => sent.push(String(message)));
  });

  await page.goto(`/?theme=night${query}`);
  await expect.poll(() => centre(page), { timeout: 30_000 }).not.toBeNull();
  return { sent, snapshots, live };
}

test('region picker · seas, straits, and a switch that re-centres and re-subscribes', async ({
  page,
}) => {
  const { sent } = await boot(page);
  const before = (await centre(page)) as number[];

  await opener(page).click();
  const panel = page.getByRole('group', { name: 'Region' });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('SEAS')).toBeVisible();
  await expect(panel.getByRole('button', { name: /North Sea/ })).toContainText('4,377');
  await expect(panel.getByText('Straits')).toBeVisible();
  await expect(panel.getByText('— live theatre')).toBeVisible();

  // a null count is the coming-soon row: listed, named, not pickable.
  const soon = panel.getByRole('button', { name: /Baltic/ });
  await expect(soon).toContainText('coming soon');
  await expect(soon).toBeDisabled();

  await panel.getByRole('button', { name: /Kattegat/ }).click();
  await expect(panel).toBeHidden();

  // the URL is the state, and the map went there.
  await expect(page).toHaveURL(/[?&]region=Kattegat/);
  await expect.poll(() => centre(page)).not.toEqual(before);

  // No snapshot is due here: from the launch view the padded box already covers the
  // Kattegat, and moveend (the one fetch path) refetches only what it does not hold.
  // What F6 actually promises is the re-subscribe below.
  // ... and the socket is re-subscribed to a bbox that covers the Kattegat.
  await expect
    .poll(
      () => {
        const boxes = sent
          .map((m) => (JSON.parse(m) as { bbox?: string }).bbox)
          .filter((b): b is string => Boolean(b))
          .map((b) => b.split(',').map(Number) as [number, number, number, number]);
        return boxes.some(
          ([w, s, e, n]) =>
            w <= KATTEGAT[0] + 0.5 && s <= KATTEGAT[1] + 0.5 &&
            e >= KATTEGAT[2] - 0.5 && n >= KATTEGAT[3] - 0.5,
        );
      },
      { timeout: 5_000 },
    )
    .toBe(true);
});

const BASE_MMSI = 999000000; // outside the valid range: cannot collide with real data
const SNAPSHOT_MMSI = BASE_MMSI + 9;

interface Feat {
  properties: { mmsi: number };
  geometry: { coordinates: [number, number] };
}
interface Source {
  serialize(): { data?: { features: Feat[] } };
}
const oursOnMap = (page: Page) =>
  page.evaluate((base) => {
    const map = (window as unknown as { __map?: { getSource(id: string): Source | undefined } })
      .__map;
    const feats = map?.getSource('vessels')?.serialize().data?.features ?? [];
    return Object.fromEntries(
      feats
        .filter((f) => f.properties.mmsi >= base)
        .map((f) => [f.properties.mmsi, f.geometry.coordinates[0]]),
    );
  }, BASE_MMSI);
const mmsisOnMap = async (page: Page) => Object.keys(await oursOnMap(page)).sort();

test('region picker · a snapshot fills in around the live fleet, it does not replace it', async ({
  page,
}) => {
  // The Kattegat's snapshot carries a ship the deltas never mentioned AND a stale
  // copy of a live one; the launch view starts empty (z=9 keeps its loaded box well
  // short of the strait, so the pick really does fetch).
  const { live } = await boot(
    page,
    (box) =>
      (box[2] ?? 0) >= KATTEGAT[2]
        ? [
            [SNAPSHOT_MMSI, 56.8, 11.5, 45, 9, 'underway', 'cargo3'],
            [BASE_MMSI, 55.0, 99.9, 45, 9, 'underway', 'tanker3'], // stale: must not win
          ]
        : [],
    '&z=9&c=3.0000,55.0000',
  );

  // two ships arrive live first…
  await expect
    .poll(
      async () => {
        live.socket?.send(
          JSON.stringify({
            ts: 1,
            interval: 30,
            vessels: [
              [BASE_MMSI, 55.0, 3.0, 45, 9, 'underway', 'tanker3'],
              [BASE_MMSI + 1, 55.1, 3.1, 45, 9, 'underway', 'cargo3'],
            ],
          }),
        );
        return (await mmsisOnMap(page)).length;
      },
      { timeout: 30_000 },
    )
    .toBe(2);

  await opener(page).click();
  await page.getByRole('group', { name: 'Region' }).getByRole('button', { name: /Kattegat/ }).click();

  // …and all three are on the map afterwards: merge by mmsi, never a wholesale swap.
  await expect
    .poll(() => mmsisOnMap(page), { timeout: 10_000 })
    .toEqual([BASE_MMSI, BASE_MMSI + 1, SNAPSHOT_MMSI].map(String));
  // the snapshot only filled the gap — it did not rewind the ship the socket owns.
  expect((await oursOnMap(page))[BASE_MMSI]).toBe(3.0);
});

test('region picker · Escape and a click outside close it', async ({ page }) => {
  await boot(page);

  await opener(page).click();
  await expect(page.getByRole('group', { name: 'Region' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('group', { name: 'Region' })).toBeHidden();

  await opener(page).click();
  await expect(page.getByRole('group', { name: 'Region' })).toBeVisible();
  await page.locator('header').getByText('ais').first().click();
  await expect(page.getByRole('group', { name: 'Region' })).toBeHidden();
});
