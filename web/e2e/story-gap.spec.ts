import { expect, test } from '@playwright/test';

/** F13 — "Unusual for this area" on the timeline, and the numbers one click away
 *  (frame 6b, "The silence, opened"). Same SSR mock pattern as story.spec.ts: the
 *  api is not up, so /ship/… is fulfilled with the markup ssr.py contracts to emit.
 */

const MMSI = 999000002;
const PATH = `/ship/northern-star-${MMSI}`;

const FLAGGED = {
  event_id: 'ev-gap-flagged',
  kind: 'gap',
  t_start: 1_700_100_000,
  t_end: 1_700_112_000,
  prose: 'Went silent — 3 hours',
  port: null,
  flag: { label: 'Unusual for this area', confidence: 0.82 },
  numbers: {
    classification: 'unusual',
    confidence: 0.82,
    cell_ships: 41,
    cell_interval_s: 300,
    neighbors_online: 14,
  },
};

const ORDINARY = {
  event_id: 'ev-gap-plain',
  kind: 'gap',
  t_start: 1_700_200_000,
  t_end: 1_700_204_000,
  prose: 'Went silent — 1 hour',
  port: null,
  numbers: { classification: 'expected', cell_ships: 12 },
};

const STORY = {
  mmsi: MMSI,
  from: 1_700_000_000,
  to: 1_700_300_000,
  window_d: 30,
  limit_line: 'Anyone can look back 30 days.',
  events: [FLAGGED, ORDINARY],
};

const CARD = {
  mmsi: MMSI,
  identity: { imo: null, name: 'Northern Star', callsign: null, flag: null, class: null,
    size_m: null, draught_m: null, destination: null, eta: null },
  sentence: 'Went silent — 3 hours',
};

const ssrBody = () => `<!doctype html>
<html lang="en" data-theme="night"><head><meta charset="utf-8">
<title>Northern Star — where she has been | ais·lake</title></head>
<body><div id="root"><h1>Northern Star</h1>
  ${STORY.events
    .map(
      (e) =>
        `<article><b>${e.prose}</b><p class="note"><a class="gap" href="?gap=${e.event_id}">See what else was nearby →</a></p></article>`,
    )
    .join('')}
</div>
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
        geometry: { type: 'LineString', coordinates: [[4.0, 51.9], [3.0, 52.6]] },
        properties: { times: [1, 2] },
        gaps: [],
      },
    }),
  );
});

const gapLink = (id: string) => `a[href="?gap=${id}"]`;

test('the flag opens the silence, and the back link returns the timeline', async ({ page }) => {
  await page.goto(PATH);
  await expect(page.getByText('Unusual for this area')).toBeVisible();

  await page.locator(gapLink(FLAGGED.event_id)).click();
  await expect(page).toHaveURL(new RegExp(`gap=${FLAGGED.event_id}`));
  await expect(page.getByRole('heading', { name: '3 hours off the air' })).toBeVisible();
  await expect(page.getByText('NORTHERN STAR → THE GAP')).toBeVisible();
  // the numbers live here and nowhere else
  await expect(page.getByText('14 ships nearby kept reporting')).toBeVisible();
  await expect(page.getByText('41 ships pass through here')).toBeVisible();
  await expect(page.getByText('every 5 minutes')).toBeVisible();
  await expect(page.getByText('WHAT WE CANNOT')).toBeVisible();
  await expect(page.getByText('We do not label it.')).toBeVisible();
  await expect(page.getByText(/method notes coming with the docs/)).toBeVisible();

  await page.getByRole('button', { name: /BACK TO THE TIMELINE/ }).click();
  await expect(page.locator('article b', { hasText: 'Went silent — 3 hours' })).toBeVisible();
  await expect(page).not.toHaveURL(/gap=/);
});

test('an ordinary gap has the same door and its own words', async ({ page }) => {
  await page.goto(`${PATH}?gap=${ORDINARY.event_id}`);
  await expect(page.getByRole('heading', { name: '1 hour off the air' })).toBeVisible();
  await expect(page.getByText('It happens here')).toBeVisible();
  await expect(page.getByText('12 ships pass through here')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Unusual for this area');
});

test('a gap id that matches nothing leaves the timeline standing', async ({ page }) => {
  await page.goto(`${PATH}?gap=nope`);
  await expect(page.locator('article b', { hasText: 'Went silent — 3 hours' })).toBeVisible();
});
