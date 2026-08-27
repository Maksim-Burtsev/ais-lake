/** The sea itself. MapLibre under the shell: basemap from /styles/{theme}.json,
 *  vessels from /v1/map/snapshot.
 *
 *  Rung 1 only (SYMBOLOGY.md §3): directional lozenges, LOZ = [7, 9, 11] px,
 *  heading + state and nothing else. Class silhouettes are rung 2 and a later task.
 */

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
// Vite must bundle the worker itself: served raw in dev it gets the @vite/client
// inject, which touches `document` and kills the worker (map never fires `load`).
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { FeatureCollection } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';

maplibregl.setWorkerUrl(workerUrl);
import { useUrlStore, type Center } from '../state/url';

/** North Sea + English Channel, the launch region (docs/design/FRAMES.md). */
const DEFAULT_CENTER: Center = [3.0, 55.0];
const DEFAULT_ZOOM = 5.5;

const LOZENGE = 'vessel-lozenge';
const SOURCE = 'vessels';
const LAYER = 'vessels';

/** SYMBOLOGY.md §3 rung 1 — three size steps, in px, across z6→z8. */
const LOZ = [7, 9, 11] as const;
const SPRITE_PX = 32; // the lozenge is drawn this long, icon-size scales it to LOZ

/** colour = state (tokens.json color.<theme>.vessel). Rung 1 carries no class. */
const STATE_COLOR: Record<'night' | 'day', Record<string, string>> = {
  night: { underway: '#EAF2F7', anchored: '#C6D8E2', moored: '#FFC97E', silent: '#FF6A52' },
  day: { underway: '#F2F6F8', anchored: '#DCE7EC', moored: '#FFC46A', silent: '#D8361F' },
};

function iconColor(theme: 'night' | 'day'): maplibregl.ExpressionSpecification {
  const c = STATE_COLOR[theme];
  return [
    'match',
    ['get', 'state'],
    'anchored', c.anchored,
    'moored', c.moored,
    'silent', c.silent,
    c.underway,
  ] as maplibregl.ExpressionSpecification;
}

/** One directional lozenge, nose up (icon-rotate turns it to cog).
 *  ponytail: a solid shape flagged sdf:true, not a real distance field — good
 *  enough at 7–11 px; the rung-2 task builds the proper SDF atlas. */
function lozenge(): ImageData {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SPRITE_PX;
  const ctx = canvas.getContext('2d')!;
  const w = SPRITE_PX * 0.34;
  ctx.beginPath();
  ctx.moveTo(SPRITE_PX / 2, 1); // nose
  ctx.lineTo(SPRITE_PX / 2 + w / 2, SPRITE_PX * 0.66);
  ctx.lineTo(SPRITE_PX / 2, SPRITE_PX - 1); // stern
  ctx.lineTo(SPRITE_PX / 2 - w / 2, SPRITE_PX * 0.66);
  ctx.closePath();
  ctx.fillStyle = '#fff';
  ctx.fill();
  return ctx.getImageData(0, 0, SPRITE_PX, SPRITE_PX);
}

interface Snapshot {
  vessels: [number, number, number, number, number, string][];
}

async function fetchVessels(map: maplibregl.Map): Promise<FeatureCollection> {
  const b = map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((v) => v.toFixed(4))
    .join(',');
  const response = await fetch(`/v1/map/snapshot?bbox=${bbox}&zoom=${map.getZoom().toFixed(2)}`);
  if (!response.ok) throw new Error(`snapshot ${response.status}`);
  const { vessels } = (await response.json()) as Snapshot;
  return {
    type: 'FeatureCollection',
    features: vessels.map(([mmsi, lat, lon, cog, , state]) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [lon, lat] },
      properties: { mmsi, cog, state },
    })),
  };
}

/** Sprite + source + layer. Re-run after every setStyle — a style swap wipes all three.
 *  Idempotent on live pieces: an existing source gets the data pushed (a theme swap can
 *  race the snapshot fetch), an existing layer gets its state colours repainted. */
function addVesselLayer(map: maplibregl.Map, theme: 'night' | 'day', data: FeatureCollection) {
  if (!map.hasImage(LOZENGE)) map.addImage(LOZENGE, lozenge(), { sdf: true });
  const source = map.getSource<maplibregl.GeoJSONSource>(SOURCE);
  if (source) {
    void source.setData(data); // resolves on render; nothing to do with it here
  } else {
    map.addSource(SOURCE, { type: 'geojson', data });
  }
  if (map.getLayer(LAYER)) {
    map.setPaintProperty(LAYER, 'icon-color', iconColor(theme));
    return;
  }
  map.addLayer({
    id: LAYER,
    type: 'symbol',
    source: SOURCE,
    layout: {
      'icon-image': LOZENGE,
      'icon-rotate': ['get', 'cog'],
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'icon-size': [
        'step',
        ['zoom'],
        LOZ[0] / SPRITE_PX,
        7, LOZ[1] / SPRITE_PX,
        8, LOZ[2] / SPRITE_PX,
      ],
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

    // The snapshot outlives style swaps; the layer is rebuilt from it each time.
    let vessels: FeatureCollection = { type: 'FeatureCollection', features: [] };

    map.on('load', () => {
      fetchVessels(map)
        .then((data) => {
          vessels = data;
        })
        .catch((error: unknown) => console.warn('map: no vessel snapshot —', error))
        .finally(() => addVesselLayer(map, useUrlStore.getState().theme, vessels));
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
      map.once('styledata', () => addVesselLayer(map, state.theme, vessels));
    });

    return () => {
      unsubscribe();
      map.remove();
    };
  }, []);

  // h-full, not inset-0: maplibre's own CSS forces position:relative on the
  // container, which silently disables absolute positioning (height collapses to 0).
  return <div ref={container} className="h-full w-full" />;
}
