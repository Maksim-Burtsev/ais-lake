import { expect, test } from '@playwright/test';
import { positionAt } from '../src/components/track';

/** F14 — "Voyage replay with scrubber & speed; live strip on top". Frames 6c
 *  (replay running) and 6f (the same row on a phone).
 *
 *  Same SSR mock pattern as story.spec.ts; the api is not up in e2e, so the page
 *  and both /v1 reads are fulfilled from here.
 */

const MMSI = 999000002;
const PATH = `/ship/northern-star-${MMSI}`;

const T0 = 1_700_000_000;
const T1 = T0 + 40_000;
/** Six fixes, a gap between the third and the fourth. */
const TIMES = [T0, T0 + 10_000, T0 + 15_000, T0 + 30_000, T0 + 35_000, T1];
const COORDS: [number, number][] = [
  [4.0, 51.9],
  [4.2, 52.0],
  [4.3, 52.1],
  [5.0, 52.4],
  [5.2, 52.5],
  [5.4, 52.6],
];
const GAPS = [{ t_start: T0 + 15_000, t_end: T0 + 30_000 }];

const STORY = {
  mmsi: MMSI,
  from: T0,
  to: T1,
  window_d: 30,
  limit_line: 'Anyone can look back 30 days.',
  events: [
    {
      event_id: 'ev-1',
      kind: 'port_call',
      t_start: T0,
      t_end: T0 + 10_000,
      prose: 'Moored in Rotterdam — 3 hours',
      port: { locode: 'NLRTM', name: 'Rotterdam' },
    },
    {
      event_id: 'ev-2',
      kind: 'gap',
      t_start: T0 + 15_000,
      t_end: T0 + 30_000,
      prose: 'Went silent — 4 hours',
      port: null,
    },
    {
      event_id: 'ev-3',
      kind: 'departure',
      t_start: T0 + 35_000,
      t_end: null,
      prose: 'Under way across the roads',
      port: null,
    },
  ],
};

const CARD = {
  mmsi: MMSI,
  identity: {
    imo: null,
    name: 'Northern Star',
    callsign: null,
    flag: null,
    class: null,
    size_m: null,
    draught_m: null,
    destination: null,
    eta: null,
  },
  sentence: 'Moored in Rotterdam — 3 hours',
  latest: { ts: Math.round(Date.now() / 1000) - 8 },
};

const ssrBody = () => `<!doctype html>
<html lang="en" data-theme="night"><head><meta charset="utf-8">
<title>Northern Star — where she has been | ais·lake</title></head>
<body><div id="root"><h1>Northern Star</h1></div>
<script type="application/json" id="story-data">${JSON.stringify({ card: CARD, story: STORY })}</script>
<script type="module">
import R from "/@react-refresh";R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};
window.$RefreshSig$=()=>(t)=>t;window.__vite_plugin_react_preamble_installed__=true;
</script>
<script type="module" src="/@vite/client"></script>
<script type="module" src="/src/main.tsx"></script>
</body></html>`;

test.beforeEach(async ({ page }) => {
  await page.route('**/ship/*', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: ssrBody() }),
  );
  await page.route('**/v1/ships/*/track**', (route) =>
    route.fulfill({
      json: {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: COORDS },
        properties: { times: TIMES },
        gaps: GAPS,
      },
    }),
  );
  await page.route(/\/v1\/ships\/\d+$/, (route) => route.fulfill({ json: CARD }));
});

/** The one piece of arithmetic on the page. Node runs it straight — no browser,
 *  no framework: the function is pure by construction. */
test('time → position: interpolates between fixes and holds through a gap', () => {
  expect(positionAt(COORDS, TIMES, GAPS, T0 - 5)).toEqual(COORDS[0]);
  expect(positionAt(COORDS, TIMES, GAPS, T1 + 5)).toEqual(COORDS[5]);

  const mid = positionAt(COORDS, TIMES, GAPS, T0 + 5_000);
  expect(mid?.[0]).toBeCloseTo(4.1, 6);
  expect(mid?.[1]).toBeCloseTo(51.95, 6);

  // Inside the silence she stays at the last real fix — no invented track.
  expect(positionAt(COORDS, TIMES, GAPS, T0 + 16_000)).toEqual(COORDS[2]);
  expect(positionAt(COORDS, TIMES, GAPS, T0 + 29_999)).toEqual(COORDS[2]);
  // …and jumps to where she reappeared, not before.
  expect(positionAt(COORDS, TIMES, GAPS, T0 + 30_000)).toEqual(COORDS[3]);

  expect(positionAt([], [], [], T0)).toBeNull();
});

const hullX = async (page: import('@playwright/test').Page): Promise<number> =>
  Number(await page.getByTestId('replay-hull').getAttribute('cx'));

test('the live strip counts her last fix, and play moves the hull', async ({ page }) => {
  await page.goto(PATH);
  await expect(page.getByTestId('live-strip')).toContainText(/AIS \d+s AGO/);

  await page.getByTestId('replay-play').click();
  await expect(page.getByTestId('replay-clock')).toBeVisible();
  const first = await hullX(page);
  await page.waitForTimeout(400);
  expect(await hullX(page)).not.toBe(first);

  // …and the timeline dims what she has not reached yet (frame 6c).
  await expect(page.locator('[data-testid="timeline-entry"][data-reached="no"]')).not.toHaveCount(0);

  // it ends by itself inside the 15-s runtime, and the strip goes back to live
  await expect(page.getByTestId('replay-clock')).toBeHidden({ timeout: 20_000 });
  await expect(page.getByTestId('live-strip')).toContainText('AIS');
});

test('scrubbing writes ?t=, and a shared ?t= comes back paused on that moment', async ({
  page,
}) => {
  await page.goto(PATH);
  const box = (await page.getByTestId('replay-bar').boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.25, box.y + box.height / 2);

  await expect(page).toHaveURL(/[?&]t=\d+/);
  const shared = new URL(page.url());
  const at = Number(shared.searchParams.get('t'));
  expect(at).toBeGreaterThan(T0);
  expect(at).toBeLessThan(T1);

  const scrubbedX = await hullX(page);
  await page.goto(`${PATH}?t=${at}`);
  // paused: the hull is where the link said, and stays there
  await expect(page.getByTestId('replay-hull')).toBeVisible();
  expect(await hullX(page)).toBeCloseTo(scrubbedX, 1);
  await page.waitForTimeout(400);
  expect(await hullX(page)).toBeCloseTo(scrubbedX, 1);
});

test('the speed buttons change how far one second of replay carries her', async ({ page }) => {
  await page.goto(`${PATH}?t=${T0}`);

  const run = async (speed: number): Promise<number> => {
    await page.goto(`${PATH}?t=${T0}`);
    await page.getByTestId(`replay-speed-${speed}`).click();
    await page.getByTestId('replay-play').click();
    await page.waitForTimeout(600);
    await page.getByTestId('replay-play').click();
    return Number(new URL(page.url()).searchParams.get('t')) - T0;
  };

  const slow = await run(1);
  const fast = await run(4);
  expect(fast).toBeGreaterThan(slow * 2);
});

test('mobile keeps the row and drops the speed buttons (6f)', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(PATH);
  await expect(page.getByTestId('replay-play')).toBeVisible();
  await expect(page.getByTestId('replay-speed-4')).toBeHidden();
  await expect(page.getByText('NOW')).toBeVisible();
});
