import { expect, test } from '@playwright/test';

/** F17/F18/F24 at the rail foot: the two download links, the copied link, the star.
 *
 *  Same route mock as story.spec.ts — the api is not up in e2e — reduced to what
 *  this row needs. The downloads are plain <a href>: the browser and the server's
 *  Content-Disposition do the rest (asserted in api/tests/test_download.py), so
 *  what is checked here is the address, not the file.
 */

const MMSI = 999000002;
const PATH = `/ship/northern-star-${MMSI}`;
const CARD = {
  mmsi: MMSI,
  identity: { imo: 9327545, name: 'Northern Star', callsign: 'PBQF', flag: 'Netherlands',
    class: 'Tanker', size_m: 120, draught_m: 8.4, destination: 'ROTTERDAM', eta: null },
  sentence: 'Moored in Rotterdam — 8 hours',
};
const STORY = { mmsi: MMSI, window_d: 30, limit_line: 'Anyone can look back 30 days.', events: [] };

const ssrBody = () => `<!doctype html>
<html lang="en" data-theme="night"><head><meta charset="utf-8"><title>Northern Star | ais·lake</title></head>
<body><div id="root"></div>
<script type="application/json" id="story-data">${JSON.stringify({ card: CARD, story: STORY })}</script>
<script type="module">
import R from "/@react-refresh";R.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};
window.$RefreshSig$=()=>(t)=>t;window.__vite_plugin_react_preamble_installed__=true;
</script>
<script type="module" src="/@vite/client"></script>
<script type="module" src="/src/main.tsx"></script>
</body></html>`;

test.beforeEach(async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
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

test('the download links point at the two file endpoints (F17)', async ({ page }) => {
  await page.goto(PATH);
  await expect(page.getByTestId('dl-csv')).toHaveAttribute('href', `/v1/ships/${MMSI}/track.csv`);
  await expect(page.getByTestId('dl-geojson')).toHaveAttribute(
    'href',
    `/v1/ships/${MMSI}/track.geojson`,
  );
});

test('copy link puts this exact view on the clipboard (F18)', async ({ page }) => {
  await page.goto(`${PATH}?t=1700000000`);
  await page.getByTestId('share').click();
  await expect(page.getByRole('status')).toContainText('Link copied');
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain(`${PATH}?t=1700000000`);
});

test('the star follows her and remembers across a reload (F24)', async ({ page }) => {
  await page.goto(PATH);
  const star = page.getByTestId('follow');
  await expect(star).toHaveText('☆');
  await star.click();
  await expect(star).toHaveText('★');
  await page.reload();
  await expect(page.getByTestId('follow')).toHaveText('★');
  expect(await page.evaluate(() => localStorage.getItem('follows'))).toBe(`[${MMSI}]`);
});

test('a fourth ship is refused while anonymous (F24)', async ({ page }) => {
  await page.goto(PATH);
  await page.evaluate(() => localStorage.setItem('follows', '[1, 2, 3]'));
  await page.reload();
  await page.getByTestId('follow').click();
  await expect(page.getByRole('status')).toContainText('Three ships is the limit');
  await expect(page.getByTestId('follow')).toHaveText('☆');
});
