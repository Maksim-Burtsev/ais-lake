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

async function boot(page: Page) {
  const sent: string[] = [];
  const snapshots: string[] = [];
  const live: { socket: WebSocketRoute | null } = { socket: null };

  await page.route('**/v1/regions', (route) => route.fulfill({ json: REGIONS }));
  await page.route('**/v1/map/snapshot*', (route) => {
    snapshots.push(route.request().url());
    return route.fulfill({ json: { region: 'north-sea', ts: 1, count: 0, vessels: [] } });
  });
  // React StrictMode mounts twice: the surviving client is the last to connect.
  await page.routeWebSocket('**/v1/live*', (ws) => {
    live.socket = ws;
    ws.onMessage((message) => sent.push(String(message)));
  });

  await page.goto('/?theme=night');
  await expect.poll(() => centre(page), { timeout: 30_000 }).not.toBeNull();
  return { sent, snapshots };
}

test('region picker · seas, straits, and a switch that re-centres and re-subscribes', async ({
  page,
}) => {
  const { sent, snapshots } = await boot(page);
  const before = (await centre(page)) as number[];

  await opener(page).click();
  const panel = page.getByRole('listbox', { name: 'Region' });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('SEAS')).toBeVisible();
  await expect(panel.getByRole('option', { name: /North Sea/ })).toContainText('4,377');
  await expect(panel.getByText('Straits')).toBeVisible();
  await expect(panel.getByText('— live theatre')).toBeVisible();

  // a null count is the coming-soon row: listed, named, not pickable.
  const soon = panel.getByRole('option', { name: /Baltic/ });
  await expect(soon).toContainText('coming soon');
  await expect(soon).toBeDisabled();

  const snapshotsBefore = snapshots.length;
  await panel.getByRole('option', { name: /Kattegat/ }).click();
  await expect(panel).toBeHidden();

  // the URL is the state, and the map went there.
  await expect(page).toHaveURL(/[?&]region=Kattegat/);
  await expect.poll(() => centre(page)).not.toEqual(before);

  // the snapshot for the new box is asked for straight away — not after the fly.
  expect(snapshots.length).toBeGreaterThan(snapshotsBefore);

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

test('region picker · Escape and a click outside close it', async ({ page }) => {
  await boot(page);

  await opener(page).click();
  await expect(page.getByRole('listbox', { name: 'Region' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('listbox', { name: 'Region' })).toBeHidden();

  await opener(page).click();
  await expect(page.getByRole('listbox', { name: 'Region' })).toBeVisible();
  await page.locator('header').getByText('ais').first().click();
  await expect(page.getByRole('listbox', { name: 'Region' })).toBeHidden();
});
