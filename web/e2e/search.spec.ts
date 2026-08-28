import { expect, test, type Page, type Route } from '@playwright/test';

/** F5 acceptance, /v1/search mocked (region.spec.ts / card.spec.ts patterns).
 *  The map is real, so "⏎ flies the map to it" is read off `window.__map` rather
 *  than from a screenshot. */

const BASE_MMSI = 999000000; // outside the valid range: cannot collide with real data
const KATTEGAT: [number, number, number, number] = [9.5, 55.5, 13.0, 58.0];

const ship = (i: number, name: string, over: Record<string, unknown> = {}) => ({
  mmsi: BASE_MMSI + i,
  name,
  flag: 'Malta',
  class: 'Tanker',
  sym: 'tanker4',
  state: 'underway',
  sentence: 'Under way at 9.8 kn',
  sog: 9.8,
  cog: 58,
  lat: 55.0,
  lon: 3.0,
  age_h: 0.1,
  ...over,
});

const GAS = [
  ship(0, 'Gas Khios'),
  ship(1, 'Gas Kalymnos', { state: 'anchored', sentence: 'At anchor', sog: 0, age_h: 18 }),
  ship(2, 'Gaskon Trader', { lat: 57.0, lon: 11.0, class: 'Cargo', sym: 'cargo3' }),
];
const SEA = { slug: 'kattegat', name: 'Kattegat', bbox: KATTEGAT, count: 318 };

/** `answering` and `searched.region` are the server's, not the client's: an empty
 *  list from a search that never ran is not a fact, and the region printed in the
 *  empty state is the box those `live` vessels were counted in. */
const body = (over: Record<string, unknown> = {}) => ({
  q: 'gas k',
  answering: true,
  ships: GAS,
  ports: [],
  seas: [SEA],
  near: null,
  searched: { live: 2412, seen_30d: 41880, region: 'North Sea' },
  ...over,
});

interface MapHandle {
  getCenter(): { lng: number; lat: number };
}
const centre = (page: Page) =>
  page.evaluate(() => {
    const map = (window as unknown as { __map?: MapHandle }).__map;
    return map ? [map.getCenter().lng, map.getCenter().lat] : null;
  });

const field = (page: Page) => page.getByRole('combobox', { name: 'Search' });
const panel = (page: Page) => page.getByRole('listbox', { name: 'Search results' });

/** `answer` is asked per request with the query, so a test can hand one query a
 *  slow reply and the next one a fast reply. */
async function boot(page: Page, answer: (q: string, route: Route) => Promise<void> | void) {
  await page.route('**/v1/search*', async (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') ?? '';
    await answer(q, route);
  });
  await page.route('**/v1/map/snapshot*', (route) =>
    route.fulfill({ json: { region: 'north-sea', ts: 1, count: 0, vessels: [] } }),
  );
  // React StrictMode mounts twice: the surviving client is the last to connect.
  await page.routeWebSocket('**/v1/live*', () => {});

  await page.goto('/?theme=night');
  await expect.poll(() => centre(page), { timeout: 30_000 }).not.toBeNull();
}

const always = (json: object) => (_q: string, route: Route) => route.fulfill({ json });

test('search · both groups, the frame\'s row structure, and the chips step back', async ({
  page,
}) => {
  await boot(page, always(body()));
  await field(page).fill('gas k');

  await expect(panel(page)).toBeVisible();
  await expect(panel(page).getByText('SHIPS', { exact: true })).toBeVisible();
  await expect(panel(page).getByText('SEAS', { exact: true })).toBeVisible();

  const rows = panel(page).getByRole('option');
  await expect(rows).toHaveCount(4);
  // class · flag · the server's own sentence, and the silhouette beside it
  await expect(rows.first()).toContainText('Tanker · Malta · Under way at 9.8 kn');
  await expect(rows.first().locator('svg path')).toHaveCount(1);
  await expect(rows.first()).toContainText('9.8 kn');
  // at anchor carries no figure: the duration is an anchorage event (M5)
  await expect(rows.nth(1)).not.toContainText('18');
  await expect(rows.nth(3)).toContainText('318 ships live');

  // the first row is focus-highlighted before a key is pressed (frame caption)
  await expect(rows.first()).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('button', { hasText: 'Tankers' }).locator('../..')).toHaveCSS(
    'opacity',
    '0.25',
  );
});

test('search · ↓ ↓ ⏎ selects a ship, sets ?sel= and flies the map', async ({ page }) => {
  await boot(page, always(body()));
  const before = (await centre(page)) as number[];

  await field(page).fill('gas k');
  await expect(panel(page)).toBeVisible();
  await field(page).press('ArrowDown');
  await field(page).press('ArrowDown');
  await expect(panel(page).getByRole('option').nth(2)).toHaveAttribute('aria-selected', 'true');
  await field(page).press('Enter');

  await expect(panel(page)).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`[?&]sel=${BASE_MMSI + 2}`));
  await expect.poll(() => centre(page), { timeout: 10_000 }).not.toEqual(before);
});

test('search · ⏎ on a sea does what picking it in the region picker does', async ({ page }) => {
  await boot(page, always(body({ ships: [] })));
  const before = (await centre(page)) as number[];

  await field(page).fill('katte');
  await expect(panel(page)).toBeVisible();
  await field(page).press('Enter');

  await expect(page).toHaveURL(/[?&]region=Kattegat/);
  await expect.poll(() => centre(page), { timeout: 10_000 }).not.toEqual(before);
});

test('search · Escape closes it', async ({ page }) => {
  await boot(page, always(body()));
  await field(page).fill('gas k');
  await expect(panel(page)).toBeVisible();
  await field(page).press('Escape');
  await expect(panel(page)).toBeHidden();
  await expect(field(page)).toBeFocused();
});

test('search · nothing to show says what was searched, in real numbers', async ({ page }) => {
  await boot(
    page,
    always(body({ ships: [], seas: [], near: ship(9, 'Gas Khios II', { class: 'Cargo' }) })),
  );
  await field(page).fill('Kapitan Zarubin');

  const empty = page.getByText('is transmitting');
  await expect(empty).toContainText('Nothing called Kapitan Zarubin is transmitting');
  await expect(page.getByText(/We searched 2,412 vessels live/)).toContainText(
    'We searched 2,412 vessels live in the North Sea and 41,880 seen in the last thirty days.',
  );
  await expect(page.getByText('TRY INSTEAD')).toBeVisible();
  await expect(page.getByText('search her nine-digit MMSI')).toBeVisible();
  await expect(page.getByText(/closest match/)).toContainText(
    'closest match: Gas Khios II — Cargo, Under way at 9.8 kn',
  );
});

test('search · a lake that never answered does not claim she is not transmitting', async ({
  page,
}) => {
  await boot(page, always(body({ answering: false, ships: [], seas: [], near: null })));
  await field(page).fill('Gas Khios');

  await expect(page.getByText('The search is not answering')).toBeVisible();
  await expect(page.getByText('is transmitting')).toBeHidden();
});

test('search · a row never states a present tense the fix cannot back', async ({ page }) => {
  await boot(
    page,
    always(
      body({
        ships: [
          // last heard forty days ago: her identity is real, her position is history
          ship(3, 'Long Gone', { state: 'moored', sentence: 'Moored', sog: 0, age_h: 960 }),
          // "Under way" below the refinery's 0.5 kn: aground and not-under-command
          // both land in this state, so the server withholds the figure on purpose
          ship(4, 'Dead Slow', { sentence: 'Under way', sog: 0 }),
        ],
        seas: [],
      }),
    ),
  );
  await field(page).fill('g');

  const rows = panel(page).getByRole('option');
  await expect(rows.first()).toContainText('40 d ago');
  await expect(rows.first()).not.toContainText('kn');
  await expect(rows.nth(1)).not.toContainText('0.0 kn');
});

test('search · a slow answer to an old query never lands on a newer one', async ({ page }) => {
  await boot(page, async (q, route) => {
    if (q === 'gas') {
      await new Promise((r) => setTimeout(r, 1500));
      return route.fulfill({ json: body({ ships: [ship(7, 'Stale Answer')], seas: [] }) });
    }
    return route.fulfill({ json: body({ ships: [ship(8, 'Fresh Answer')], seas: [] }) });
  });

  await field(page).fill('gas');
  await page.waitForTimeout(400); // long enough that the slow request is in flight
  await field(page).fill('gas khios');
  await expect(panel(page).getByRole('option').first()).toContainText('Fresh Answer');

  await page.waitForTimeout(2000); // the stale reply lands in here
  await expect(panel(page).getByRole('option')).toHaveCount(1);
  await expect(panel(page).getByRole('option').first()).toContainText('Fresh Answer');
});
