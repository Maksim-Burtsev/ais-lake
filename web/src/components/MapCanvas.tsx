/** The sea itself. MapLibre under the shell: basemap from /styles/{theme}.json,
 *  vessels from /v1/map/snapshot.
 *
 *  Rungs 1 and 2 (SYMBOLOGY.md §3). Below z9: directional lozenges, LOZ =
 *  [7, 9, 11] px — heading + state, class deliberately unreadable. From z9: the
 *  eight class silhouettes at STEP = [10…28] px, with the five states' shape cues
 *  and a cast shadow. Rung 0 (density field) and rung 3 (true-scale footprints)
 *  are not built yet — rung 2 simply continues above z12.
 */

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
// Vite must bundle the worker itself: served raw in dev it gets the @vite/client
// inject, which touches `document` and kills the worker (map never fires `load`).
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { Feature, FeatureCollection, Point } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';

maplibregl.setWorkerUrl(workerUrl);
import { startLive, type Vessel } from '../state/live';
import { LOZ, NOMINAL_L, iconOf, sprites, SPRITE_PIXEL_RATIO } from '../map/hulls';
import tokens from '../theme/tokens.json';
import { useUrlStore, type Center } from '../state/url';

/** North Sea + English Channel, the launch region (docs/design/FRAMES.md). */
const DEFAULT_CENTER: Center = [3.0, 55.0];
const DEFAULT_ZOOM = 5.5;

const SOURCE = 'vessels';
const LOZ_LAYER = 'vessels-loz';
const HULL_LAYER = 'vessels-hull';
const SHADOW_LAYER = 'vessels-shadow';
/** Rung 1 ends and rung 2 begins here (SYMBOLOGY.md §3). */
const RUNG2_ZOOM = 9;
/** `so = max(1.6, L * .09)` from the frame, frozen at the mid length step: the
 *  shadow is one offset for the whole fleet, not a per-ship computation. */
const SHADOW_OFFSET: [number, number] = [1.8, 2.2];

/** colour = state (tokens.json color.<theme>.vessel); shape = class. */
function iconColor(theme: 'night' | 'day'): maplibregl.ExpressionSpecification {
  const c = tokens.color[theme].vessel;
  return [
    'match',
    ['get', 'state'],
    'anchored', c['anchor.lit'],
    'moored', c['moor.lit'],
    'silent', c.silent,
    'selected', c.halo,
    c['hull.lit'],
  ] as maplibregl.ExpressionSpecification;
}

type VesselFeature = Feature<
  Point,
  { mmsi: number; cog: number; state: string; icon: string; px: number }
>;

/** The wire row, resolved to a sprite: `sym` carries class + length step, and a
 *  row from before that token existed simply draws the unknown capsule. */
const feature = ([mmsi, lat, lon, cog, sog, state, sym]: Vessel): VesselFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lon, lat] },
  properties: { mmsi, cog, state, ...iconOf(sym ?? 'unknown2', state, sog) },
});

/** The current viewport, in the api's bbox order. */
function bboxOf(map: maplibregl.Map): string {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',');
}

async function fetchVessels(map: maplibregl.Map): Promise<Vessel[]> {
  const query = `bbox=${bboxOf(map)}&zoom=${map.getZoom().toFixed(2)}`;
  const response = await fetch(`/v1/map/snapshot?${query}`);
  if (!response.ok) throw new Error(`snapshot ${response.status}`);
  const { vessels } = (await response.json()) as { vessels: Vessel[] };
  return vessels;
}

/** Sprites + source + layers. Re-run after every setStyle — a style swap wipes all
 *  of them, images included. Idempotent on live pieces: an existing source gets the
 *  data pushed (a theme swap can race the snapshot fetch), existing layers get their
 *  state colours repainted. */
function addVesselLayer(map: maplibregl.Map, theme: 'night' | 'day', data: FeatureCollection) {
  if (!map.hasImage('loz')) {
    for (const [name, image] of Object.entries(sprites())) {
      map.addImage(name, image, { sdf: true, pixelRatio: SPRITE_PIXEL_RATIO });
    }
  }
  const source = map.getSource<maplibregl.GeoJSONSource>(SOURCE);
  if (source) {
    void source.setData(data); // resolves on render; nothing to do with it here
  } else {
    map.addSource(SOURCE, { type: 'geojson', data });
  }
  if (map.getLayer(HULL_LAYER)) {
    map.setPaintProperty(LOZ_LAYER, 'icon-color', iconColor(theme));
    map.setPaintProperty(HULL_LAYER, 'icon-color', iconColor(theme));
    map.setPaintProperty(SHADOW_LAYER, 'icon-color', tokens.color[theme].vessel.shadow);
    return;
  }

  const common: maplibregl.SymbolLayerSpecification['layout'] = {
    'icon-rotate': ['get', 'cog'],
    'icon-rotation-alignment': 'map',
    'icon-allow-overlap': true,
    'icon-ignore-placement': true,
  };

  map.addLayer({
    id: LOZ_LAYER,
    type: 'symbol',
    source: SOURCE,
    maxzoom: RUNG2_ZOOM,
    layout: {
      ...common,
      'icon-image': 'loz',
      'icon-size': ['step', ['zoom'],
        LOZ[0] / NOMINAL_L, 7, LOZ[1] / NOMINAL_L, 8, LOZ[2] / NOMINAL_L],
    },
    paint: { 'icon-color': iconColor(theme) },
  });
  // the cast shadow: the same silhouette, offset and dimmed, under the hull.
  // A silent ship throws none — it is a ghost, not a hull in the water.
  map.addLayer({
    id: SHADOW_LAYER,
    type: 'symbol',
    source: SOURCE,
    minzoom: RUNG2_ZOOM,
    filter: ['!=', ['get', 'state'], 'silent'],
    layout: {
      ...common,
      'icon-image': ['get', 'icon'],
      'icon-size': ['/', ['get', 'px'], NOMINAL_L],
    },
    paint: {
      'icon-translate': SHADOW_OFFSET,
      'icon-color': tokens.color[theme].vessel.shadow,
      'icon-opacity': tokens.color[theme].vessel['shadow.opacity'],
    },
  });
  map.addLayer({
    id: HULL_LAYER,
    type: 'symbol',
    source: SOURCE,
    minzoom: RUNG2_ZOOM,
    layout: {
      ...common,
      'icon-image': ['get', 'icon'],
      'icon-size': ['/', ['get', 'px'], NOMINAL_L],
    },
    paint: { 'icon-color': iconColor(theme) },
  });
}

export function MapCanvas() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;
    const { theme, zoom, center } = useUrlStore.getState();
    const map = new maplibregl.Map({
      container: container.current,
      style: `/styles/${theme}.json`,
      center: center ?? DEFAULT_CENTER,
      zoom: zoom ?? DEFAULT_ZOOM,
      attributionControl: { compact: true },
    });

    // The fleet outlives style swaps; the layer is rebuilt from it each time.
    // A Map, not a FeatureCollection: GeoJSON sources have no partial update, so
    // every live delta is merged here by MMSI and the whole collection re-pushed.
    const fleet = new Map<number, VesselFeature>();
    const collection = (): FeatureCollection => ({
      type: 'FeatureCollection',
      features: [...fleet.values()],
    });
    const upsert = (rows: Vessel[]) => {
      for (const row of rows) fleet.set(row[0], feature(row));
    };

    // The socket does not wait for the basemap: a slow tile host must not stall
    // the live feed, and the LIVE dot has to tell the truth from the first second.
    // setData is optional-chained — before `load` there is no source to push to,
    // and the layer is built from `collection()` when there is.
    const stopLive = startLive(
      () => bboxOf(map),
      (rows) => {
        upsert(rows);
        void map.getSource<maplibregl.GeoJSONSource>(SOURCE)?.setData(collection());
      },
    );

    map.on('load', () => {
      fetchVessels(map)
        .then(upsert)
        .catch((error: unknown) => console.warn('map: no vessel snapshot —', error))
        .finally(() => addVesselLayer(map, useUrlStore.getState().theme, collection()));
    });

    map.on('moveend', () => {
      const c = map.getCenter();
      useUrlStore.getState().patch({ zoom: map.getZoom(), center: [c.lng, c.lat] });
    });

    let painted = useUrlStore.getState().theme;
    const unsubscribe = useUrlStore.subscribe((state) => {
      if (state.theme === painted) return;
      painted = state.theme;
      map.setStyle(`/styles/${state.theme}.json`);
      map.once('styledata', () => addVesselLayer(map, state.theme, collection()));
    });

    // The e2e spec drives the vessel source through this handle (dev only).
    if (import.meta.env.DEV) (window as unknown as { __map?: maplibregl.Map }).__map = map;

    return () => {
      stopLive();
      unsubscribe();
      map.remove();
    };
  }, []);

  // h-full, not inset-0: maplibre's own CSS forces position:relative on the
  // container, which silently disables absolute positioning (height collapses to 0).
  return <div ref={container} className="h-full w-full" />;
}
