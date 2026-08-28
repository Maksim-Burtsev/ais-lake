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
import { startLive, useLiveStore, type Vessel } from '../state/live';
import { LOZ, NOMINAL_L, iconOf, sprites, SPRITE_PIXEL_RATIO } from '../map/hulls';
import tokens from '../theme/tokens.json';
import { useUrlStore, type Center, type VesselFilter } from '../state/url';

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

/** F7 — the coral pulse over every silent ship, `om-pulse` from the frame:
 *  a 120-px ring scaling .26 -> 1 and fading .62 -> 0 over 3.6 s, twice, half a
 *  period apart. Two layers, one rAF loop, radius + stroke opacity per frame. */
const PULSE_LAYERS = ['vessels-pulse-a', 'vessels-pulse-b'] as const;
const PULSE_MS = 3_600;
const PULSE_R = 60;
const PULSE_MIN = 0.26;
const PULSE_OPACITY = 0.62;
const pulseAt = (phase: number) => ({
  r: PULSE_R * (PULSE_MIN + (1 - PULSE_MIN) * phase),
  o: PULSE_OPACITY * (1 - phase),
});

/** F7 — a filter does not hide the rest of the fleet, it drops it to 22% so the
 *  matches are the only thing alive on the water (the frame's own word). */
const DIM = 0.22;
const MATCH: Record<VesselFilter, maplibregl.ExpressionSpecification> = {
  tankers: ['==', ['get', 'cls'], 'tanker'],
  cargo: ['==', ['get', 'cls'], 'cargo'],
  anchored: ['==', ['get', 'state'], 'anchored'],
  silent: ['==', ['get', 'state'], 'silent'],
};

const iconOpacity = (
  filter: VesselFilter | undefined,
  base: number,
): number | maplibregl.ExpressionSpecification =>
  filter === undefined ? base : ['case', MATCH[filter], base, base * DIM];

/** Dim + ring visibility, on layer creation and on every filter change. */
function applyFilter(map: maplibregl.Map, filter: VesselFilter | undefined, theme: 'night' | 'day') {
  map.setPaintProperty(LOZ_LAYER, 'icon-opacity', iconOpacity(filter, 1));
  map.setPaintProperty(HULL_LAYER, 'icon-opacity', iconOpacity(filter, 1));
  map.setPaintProperty(
    SHADOW_LAYER,
    'icon-opacity',
    iconOpacity(filter, tokens.color[theme].vessel['shadow.opacity']),
  );
  for (const id of PULSE_LAYERS) {
    map.setLayoutProperty(id, 'visibility', filter === 'silent' ? 'visible' : 'none');
  }
}

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
  { mmsi: number; cog: number; state: string; icon: string; cls: string; px: number }
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
  const filter = useUrlStore.getState().filter;
  if (map.getLayer(HULL_LAYER)) {
    map.setPaintProperty(LOZ_LAYER, 'icon-color', iconColor(theme));
    map.setPaintProperty(HULL_LAYER, 'icon-color', iconColor(theme));
    map.setPaintProperty(SHADOW_LAYER, 'icon-color', tokens.color[theme].vessel.shadow);
    for (const id of PULSE_LAYERS) {
      map.setPaintProperty(id, 'circle-stroke-color', tokens.color[theme].vessel.silent);
    }
    applyFilter(map, filter, theme);
    return;
  }

  // The pulses go under the fleet: a ring is the ground the ship stands on.
  PULSE_LAYERS.forEach((id, i) => {
    const { r, o } = pulseAt(i * 0.5);
    map.addLayer({
      id,
      type: 'circle',
      source: SOURCE,
      filter: ['==', ['get', 'state'], 'silent'],
      paint: {
        'circle-radius': r,
        'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-color': tokens.color[theme].vessel.silent,
        'circle-stroke-width': i === 0 ? 1.6 : 1,
        'circle-stroke-opacity': o,
      },
    });
  });

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
  applyFilter(map, filter, theme);
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
    // ponytail: rAF only while the silent chip is up AND something is silent —
    // no silent ships today (the gap classifier lands with M3), so the loop
    // simply never starts. Reduced motion gets the rings, frozen mid-pulse.
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let raf = 0;
    const paintPulse = (t: number) => {
      PULSE_LAYERS.forEach((id, i) => {
        if (!map.getLayer(id)) return;
        const { r, o } = pulseAt((t / PULSE_MS + i * 0.5) % 1);
        map.setPaintProperty(id, 'circle-radius', r);
        map.setPaintProperty(id, 'circle-stroke-opacity', o);
      });
    };
    const tick = (t: number) => {
      paintPulse(t);
      raf = requestAnimationFrame(tick);
    };
    const syncPulse = () => {
      const on =
        !still &&
        useUrlStore.getState().filter === 'silent' &&
        useLiveStore.getState().silent > 0;
      if (on && !raf) raf = requestAnimationFrame(tick);
      if (!on && raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
    };

    /** F7 count line: silent ships inside the viewport, from the fleet we already
     *  hold — queryRenderedFeatures would only see what is currently painted. */
    const recount = () => {
      const bounds = map.getBounds();
      let n = 0;
      for (const f of fleet.values()) {
        if (f.properties.state !== 'silent') continue;
        const [lon, lat] = f.geometry.coordinates as [number, number];
        if (bounds.contains([lon, lat])) n += 1;
      }
      useLiveStore.getState().setSilent(n);
      syncPulse();
    };

    const upsert = (rows: Vessel[]) => {
      for (const row of rows) fleet.set(row[0], feature(row));
      recount();
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
      recount();
    });

    let painted = useUrlStore.getState().theme;
    let filtered = useUrlStore.getState().filter;
    const unsubscribe = useUrlStore.subscribe((state) => {
      if (state.filter !== filtered) {
        filtered = state.filter;
        if (map.getLayer(HULL_LAYER)) applyFilter(map, state.filter, state.theme);
        if (still) paintPulse(PULSE_MS / 2);
        syncPulse();
      }
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
      if (raf) cancelAnimationFrame(raf);
      map.remove();
    };
  }, []);

  // h-full, not inset-0: maplibre's own CSS forces position:relative on the
  // container, which silently disables absolute positioning (height collapses to 0).
  return <div ref={container} className="h-full w-full" />;
}
