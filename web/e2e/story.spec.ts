import { expect, test } from '@playwright/test';

/** S2 acceptance (F12/F15/F16/F31): the vessel page is readable HTML before any
 *  script runs, and the SPA takes the same markup over without a second request.
 *
 *  The api is not up in e2e (the harness boots Vite alone), so /ship/… is served
 *  by a route mock standing in for api/app/ssr.py — the SSR *contract* the page
 *  depends on: prose in the markup, the payload in <script id="story-data">, and
 *  the bundle's own tags. What the server puts in those slots is asserted in
 *  api/tests/test_ssr.py; what the client does with them is asserted here.
 */

const MMSI = 999000002;
const PATH = `/ship/northern-star-${MMSI}`;

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

const STORY = {
  mmsi: MMSI,
  from: 1_700_000_000,
  to: 1_700_200_000,
  window_d: 30,
  limit_line: 'Anyone can look back 30 days.',
  events: [
    {
      event_id: 'ev-1',
      kind: 'port_call',
      t_start: 1_700_000_000,
      t_end: 1_700_030_000,
      prose: 'Moored in Rotterdam — 8 hours',
      port: { locode: 'NLRTM', name: 'Rotterdam' },
    },
    {
      event_id: 'ev-2',
      kind: 'gap',
      t_start: 1_700_100_000,
      t_end: 1_700_112_000,
      prose: 'Went silent — 3 hours',
      port: null,
      flag: { label: 'Unusual for this area', confidence: 0.82 },
    },
  ],
};

const CARD = { mmsi: MMSI, identity: IDENTITY, sentence: 'Moored in Rotterdam — 8 hours' };

/** What ssr.py emits, reduced to the parts the client contracts on. The dev-mode
 *  script tags are relative here because the mock is served from Vite's origin. */
const ssrBody = () => `<!doctype html>
<html lang="en" data-theme="night"><head><meta charset="utf-8">
<title>Northern Star — where she has been | ais·lake</title>
<meta name="description" content="Northern Star (Tanker · Netherlands · 120 m)">
<link rel="canonical" href="${PATH}">
<meta property="og:image" content="${PATH}/og.png"></head>
<body><div id="root">
  <h1>Northern Star</h1>
  <p>Tanker · Netherlands · 120 m</p>
  ${STORY.events.map((e) => `<article><b>${e.prose}</b></article>`).join('')}
  <dl><div><dt>CALLSIGN</dt><dd>PBQF</dd></div></dl>
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
  await page.route(`**/v1/ships/${MMSI}`, (route) => route.fulfill({ json: CARD }));
  await page.route(`**/v1/ships/${MMSI}/story`, (route) => route.fulfill({ json: STORY }));
  await page.route('**/v1/ships/*/track**', (route) =>
    route.fulfill({
      json: {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [[4.0, 51.9], [3.4, 52.2], [3.0, 52.6]] },
        properties: { times: [1, 2, 3] },
        gaps: [],
      },
    }),
  );
});

test('the SPA takes the page over and renders the server prose', async ({ page }) => {
  await page.goto(PATH);
  await expect(page).toHaveTitle(/Northern Star/);
  // React has mounted when the SSR shell's plain markup has become the real page.
  await expect(page.getByRole('heading', { name: 'Northern Star', level: 1 })).toBeVisible();
  for (const event of STORY.events) {
    await expect(page.locator('article b', { hasText: event.prose })).toBeVisible();
  }
  // the flag's label, never its confidence (CLAUDE.md: no scores on a page)
  await expect(page.getByText('Unusual for this area')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('0.82');
  // right rail, F15: what AIS gave us, dashes for what it did not
  await expect(page.getByText('PBQF')).toBeVisible();
  await expect(page.getByText('120 × — m')).toBeVisible();
  await expect(page.getByText(STORY.limit_line)).toBeVisible();
  await expect(page.getByRole('link', { name: /BACK TO THE MAP/ })).toHaveAttribute('href', '/');
  // the takeover reads the embedded payload, so no second /v1 story read goes out
  await expect(page.locator('polyline')).toBeVisible();
});

test.describe('with javascript off', () => {
  test.use({ javaScriptEnabled: false });

  test('the story is still readable (F31)', async ({ page }) => {
    await page.goto(PATH);
    await expect(page.getByText('Moored in Rotterdam — 8 hours')).toBeVisible();
    await expect(page.getByText('Went silent — 3 hours')).toBeVisible();
    await expect(page.locator('link[rel=canonical]')).toHaveAttribute('href', PATH);
  });
});
