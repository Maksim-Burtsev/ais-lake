import { expect, test, type WebSocketRoute } from '@playwright/test';

/** F1 end to end, with the socket mocked: no api, no Redis, no timing luck.
 *  Assertions are on state and attributes — shell.spec.ts kills animations, so a
 *  screenshot could never prove that anything moved. */

const MMSI = 999999999; // outside the valid MMSI range: cannot collide with real data
const frame = (lat: number, lon: number, interval = 10) =>
  JSON.stringify({ ts: 1, interval, vessels: [[MMSI, lat, lon, 90, 12.4, 'underway']] });

/** The vessel source as MapCanvas exposes it in dev: enough of maplibre's shape
 *  to read back what the map was actually handed. */
interface MapHandle {
  getSource(id: string): { serialize(): { data?: VesselCollection } } | undefined;
}
interface VesselCollection {
  features: { properties: { mmsi: number }; geometry: { coordinates: number[] } }[];
}

/** Where the map thinks our test ship is, straight out of the vessel source. */
async function shipAt(page: import('@playwright/test').Page): Promise<number[] | null> {
  return page.evaluate((mmsi) => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    const data = map?.getSource('vessels')?.serialize().data;
    return data?.features.find((f) => f.properties.mmsi === mmsi)?.geometry.coordinates ?? null;
  }, MMSI);
}

test('live layer · ships move, the cadence switches, the dot tells the truth', async ({ page }) => {
  const received: string[] = [];
  // React StrictMode mounts the map twice in dev, so the surviving client is the
  // second one to connect: always keep the newest route, never the first.
  const live: { socket: WebSocketRoute | null } = { socket: null };
  let refuse = false;

  await page.routeWebSocket('**/v1/live*', (ws) => {
    if (refuse) {
      void ws.close(); // the reconnect attempt: stay down so the LIVE dot has to say so
      return;
    }
    live.socket = ws;
    ws.onMessage((message) => received.push(String(message)));
  });

  await page.goto('/?theme=night');

  // 1. ships visibly move: two frames, one MMSI, different coordinates.
  // Frames are re-sent while polling — the first may land before the basemap
  // does (the socket does not wait for it) or before the remount settles.
  const seaMoves = async (lat: number, lon: number) => {
    live.socket?.send(frame(lat, lon));
    return shipAt(page);
  };
  await expect.poll(() => seaMoves(55.0, 3.0), { timeout: 30_000 }).toEqual([3, 55]);
  await expect.poll(() => seaMoves(55.2, 3.4), { timeout: 20_000 }).toEqual([3.4, 55.2]);

  // 2. the interval selector patches the open socket and restarts the sweep.
  await page.selectOption('header select', '30');
  const lastInterval = () => {
    const last = received.at(-1);
    return last === undefined ? null : (JSON.parse(last) as { interval: number }).interval;
  };
  await expect.poll(lastInterval).toBe(30);
  await expect(page.locator('header [data-sweep]')).toHaveCSS('animation-duration', '30s');
  await expect(page.locator('header')).toContainText('LIVE · 30s');
  // 5 s is a free-account floor; anonymous may look at it, not pick it.
  await expect(page.locator('header select option[value="5"]')).toBeDisabled();

  // 3. the LIVE dot reflects socket health.
  await expect(page.locator('header [data-live="live"]')).toBeVisible();
  refuse = true;
  await live.socket?.close();
  await expect(page.locator('header [data-live="down"]')).toBeVisible();
});
