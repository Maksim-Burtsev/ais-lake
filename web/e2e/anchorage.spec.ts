import { expect, test, type Page } from '@playwright/test';

/** F9 acceptance: the polygons come from the API and hovering an anchorage lifts
 *  it and names it. /v1/map/ports is mocked with two squares around the map centre
 *  — a real anchorage is a ragged multipolygon whose interior no fixed cursor
 *  position can be trusted to hit. The queue numbers are F19 (M5), so the sentence
 *  is deliberately a placeholder and this spec pins that it stays honest. */

const SHOTS = 'test-results';
const LON = 4.0;
const LAT = 52.0;
/** The anchorage sits left of centre, the port right: one cursor position each. */
const square = (lon: number, kind: string, name: string) => ({
  type: 'Feature',
  properties: { locode: 'NLTST', name, kind },
  geometry: {
    type: 'Polygon',
    coordinates: [
      [
        [lon - 0.1, LAT - 0.1],
        [lon + 0.1, LAT - 0.1],
        [lon + 0.1, LAT + 0.1],
        [lon - 0.1, LAT + 0.1],
        [lon - 0.1, LAT - 0.1],
      ],
    ],
  },
});
const PORTS = {
  type: 'FeatureCollection',
  features: [square(LON - 0.3, 'anchorage', 'Testhaven'), square(LON + 0.3, 'port', 'Testhaven')],
};

interface MapHandle {
  getLayer(id: string): unknown;
  getPaintProperty(id: string, name: string): unknown;
  project(lngLat: [number, number]): { x: number; y: number };
  getSource(id: string): { serialize(): { data?: { features: unknown[] } } } | undefined;
  queryRenderedFeatures(
    point: [number, number],
    options: { layers: string[] },
  ): { id?: number | string; state: { hover?: boolean } }[];
}

async function boot(page: Page, theme = 'night') {
  await page.route('**/v1/map/ports', (route) => route.fulfill({ json: PORTS }));
  await page.routeWebSocket('**/v1/live*', () => {});
  await page.goto(`/?theme=${theme}&z=9&c=${LON.toFixed(4)},${LAT.toFixed(4)}`);
  await expect
    .poll(
      () =>
        page.evaluate(() =>
          Boolean((window as unknown as { __map?: MapHandle }).__map?.getLayer('ports-fill')),
        ),
      { timeout: 30_000 },
    )
    .toBe(true);
  await expect
    .poll(
      () =>
        page.evaluate(
          () =>
            (window as unknown as { __map?: MapHandle }).__map
              ?.getSource('ports')
              ?.serialize().data?.features.length ?? 0,
        ),
      { timeout: 15_000 },
    )
    .toBe(2);
}

/** Page coordinates of a lon/lat (card.spec.ts). */
async function pointFor(page: Page, lon: number, lat: number) {
  const box = await page.locator('canvas.maplibregl-canvas').boundingBox();
  const at = await page.evaluate(
    ([lng, la]) => {
      const p = (window as unknown as { __map?: MapHandle }).__map!.project([lng!, la!]);
      return [p.x, p.y];
    },
    [lon, lat],
  );
  return { x: box!.x + at[0]!, y: box!.y + at[1]! };
}

/** How many rendered port features currently carry the hover state. */
const hovered = (page: Page, x: number, y: number) =>
  page.evaluate(
    ([px, py]) =>
      (window as unknown as { __map?: MapHandle })
        .__map!.queryRenderedFeatures([px!, py!], { layers: ['ports-fill'] })
        .filter((f) => f.state.hover === true).length,
    [x, y],
  );

const tip = (page: Page) => page.getByRole('tooltip');

test('anchorage · the polygons are drawn under the fleet, in both themes', async ({ page }) => {
  await boot(page);
  const order = await page.evaluate(() =>
    (window as unknown as { __map?: { getStyle(): { layers: { id: string }[] } } })
      .__map!.getStyle()
      .layers.map((l) => l.id),
  );
  expect(order).toContain('ports-fill');
  expect(order).toContain('ports-line');
  expect(order.indexOf('ports-fill')).toBeLessThan(order.indexOf('vessels-hull'));

  // the day palette rebuilds every layer: the polygons have to come back with it.
  await page.getByRole('button', { name: /Switch to daylight/ }).click();
  await expect(page).toHaveURL(/[?&]theme=day/);
  await expect
    .poll(
      () =>
        page.evaluate(() =>
          (window as unknown as { __map?: MapHandle }).__map?.getPaintProperty(
            'ports-fill',
            'fill-color',
          ),
        ),
      {
        timeout: 30_000,
      },
    )
    .toBe('#B8145A');
});

test('anchorage · hovering one lifts it and offers the queue, honestly', async ({ page }) => {
  await boot(page);
  const anchorage = await pointFor(page, LON - 0.3, LAT);

  await page.mouse.move(anchorage.x, anchorage.y);
  await expect(tip(page)).toBeVisible();
  await expect(tip(page)).toHaveText('Testhaven anchorage — queue counting starts soon');
  expect(await hovered(page, anchorage.x, anchorage.y)).toBe(1);
  await page.screenshot({ path: `${SHOTS}/anchorage-hover.png` });

  // a port polygon is drawn, but it has no queue to offer: no lift, no tooltip.
  const port = await pointFor(page, LON + 0.3, LAT);
  await page.mouse.move(port.x, port.y);
  await expect(tip(page)).toBeHidden();
  expect(await hovered(page, port.x, port.y)).toBe(0);

  // and off the polygons entirely the lift is dropped, not left stuck on.
  await page.mouse.move(anchorage.x, anchorage.y);
  await expect(tip(page)).toBeVisible();
  await page.mouse.move(anchorage.x, anchorage.y + 300);
  await expect(tip(page)).toBeHidden();
  expect(await hovered(page, anchorage.x, anchorage.y)).toBe(0);
});
