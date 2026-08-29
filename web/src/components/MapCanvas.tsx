/** The sea itself. MapLibre under the shell: basemap from /styles/{theme}.json,
 *  vessels from /v1/map/snapshot.
 *
 *  Rungs 1 and 2 (SYMBOLOGY.md §3). Below z9: directional lozenges, LOZ =
 *  [7, 9, 11] px — heading + state, class deliberately unreadable. From z9: the
 *  eight class silhouettes at STEP = [10…28] px, with the five states' shape cues
 *  and a cast shadow. Rung 0 (density field) and rung 3 (true-scale footprints)
 *  are not built yet — rung 2 simply continues above z12.
 */

import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
// Vite must bundle the worker itself: served raw in dev it gets the @vite/client
// inject, which touches `document` and kills the worker (map never fires `load`).
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { Feature, FeatureCollection, Point } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';

maplibregl.setWorkerUrl(workerUrl);
import { startLive, useLiveStore, type Vessel } from '../state/live';
import { LOZ, NOMINAL_L, iconOf, sprites, SPRITE_PIXEL_RATIO } from '../map/hulls';
import { mapView, type Bbox } from '../map/view';
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

/** The selected ship is never dimmed. `icon-opacity` does not affect hit-testing,
 *  so she is pickable through any filter and her card opens with her full identity
 *  while she sits at 22% on the water — and F10's "paste the URL in incognito ->
 *  identical view" then hands someone a URL whose subject is nearly invisible.
 *  With nothing selected the expression is exactly what it always was: the term is
 *  there only when there is a selection to except. */
const iconOpacity = (
  filter: VesselFilter | undefined,
  base: number,
  selection?: number,
): number | maplibregl.ExpressionSpecification => {
  if (filter === undefined) return base;
  const match: maplibregl.ExpressionSpecification =
    selection === undefined
      ? MATCH[filter]
      : ['any', ['==', ['get', 'mmsi'], selection], MATCH[filter]];
  return ['case', match, base, base * DIM];
};

/** Dim + ring visibility, on layer creation and on every filter change. Selection
 *  rides along because the two write different properties on the same layers, and
 *  a selection change under an active filter has to repaint the opacity too. */
function applyFilter(
  map: maplibregl.Map,
  filter: VesselFilter | undefined,
  theme: 'night' | 'day',
  selection?: number,
) {
  map.setPaintProperty(LOZ_LAYER, 'icon-opacity', iconOpacity(filter, 1, selection));
  map.setPaintProperty(HULL_LAYER, 'icon-opacity', iconOpacity(filter, 1, selection));
  map.setPaintProperty(
    SHADOW_LAYER,
    'icon-opacity',
    iconOpacity(filter, tokens.color[theme].vessel['shadow.opacity'], selection),
  );
  for (const id of PULSE_LAYERS) {
    map.setLayoutProperty(id, 'visibility', filter === 'silent' ? 'visible' : 'none');
  }
}

/** F8/F10 — the selected ship is PAINT, not data. The fleet's whole collection is
 *  re-pushed on every change (see `upsert`), so rebuilding it per click would cost
 *  O(fleet) for one changed feature. Instead both layers carry a `case` on mmsi and
 *  a selection change is two setters. 0 is the sentinel: MMSIs are nine digits, so
 *  no ship can ever be it, and the expression stays well-formed with nothing chosen. */
const NO_SELECTION = 0;

/** colour = state (tokens.json color.<theme>.vessel); shape = class. The selected
 *  ship substitutes the state SYMBOLOGY.md §2 already has a colour for. */
function iconColor(
  theme: 'night' | 'day',
  selection = NO_SELECTION,
): maplibregl.ExpressionSpecification {
  const c = tokens.color[theme].vessel;
  return [
    'match',
    ['case', ['==', ['get', 'mmsi'], selection], 'selected', ['get', 'state']],
    'anchored', c['anchor.lit'],
    'moored', c['moor.lit'],
    'silent', c.silent,
    'selected', c.halo,
    c['hull.lit'],
  ] as maplibregl.ExpressionSpecification;
}

/** ... and the same substitution on the silhouette: `${cls}${step}-selected` is
 *  already in the atlas (hulls.ts STATES), so the double halo costs no new sprite.
 *  It is `sym`, not `cls`: sprites are per length step, so the class alone no
 *  longer names an image and a selected ship built from it would vanish. */
const selectedIcon = (selection = NO_SELECTION): maplibregl.ExpressionSpecification => [
  'case',
  ['==', ['get', 'mmsi'], selection],
  ['concat', ['get', 'sym'], '-selected'],
  ['get', 'icon'],
];

/** On layer creation, on every selection change, and after every style swap. */
function applySelection(
  map: maplibregl.Map,
  selection: number | undefined,
  theme: 'night' | 'day',
) {
  map.setLayoutProperty(HULL_LAYER, 'icon-image', selectedIcon(selection));
  // The shadow is the same silhouette one step down-right, so it has to follow the
  // hull into the `-selected` cell. That cell draws no wake (SYMBOLOGY.md §2 gives
  // selected the double halo instead), and a shadow still drawing `-u{bucket}`
  // leaves a ~31 px dark ribbon astern of a hull that no longer has a bright one.
  map.setLayoutProperty(SHADOW_LAYER, 'icon-image', selectedIcon(selection));
  map.setPaintProperty(HULL_LAYER, 'icon-color', iconColor(theme, selection));
  // rung 1 keeps its lozenge — class is deliberately unreadable there — and only
  // takes the halo colour.
  map.setPaintProperty(LOZ_LAYER, 'icon-color', iconColor(theme, selection));
}

type VesselFeature = Feature<
  Point,
  { mmsi: number; cog: number; state: string; icon: string; cls: string; sym: string }
>;

/** The wire row, resolved to a sprite: `sym` carries class + length step, and a
 *  row from before that token existed simply draws the unknown capsule. */
const feature = ([mmsi, lat, lon, cog, sog, state, sym]: Vessel): VesselFeature => ({
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [lon, lat] },
  properties: { mmsi, cog, state, ...iconOf(sym ?? 'unknown2', state, sog) },
});

function boundsOf(map: maplibregl.Map): Bbox {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
}

/** A snapshot is fetched for rather more than the visible box, so a small pan
 *  costs nothing. Refetch only happens when the view leaves what was loaded. */
const MARGIN = 0.3;
const pad = ([w, s, e, n]: Bbox): Bbox => {
  const dx = (e - w) * MARGIN;
  const dy = (n - s) * MARGIN;
  return [
    Math.max(-180, w - dx), Math.max(-90, s - dy),
    Math.min(180, e + dx), Math.min(90, n + dy),
  ];
};
/** The socket subscribes to the same padded box the snapshot loads, so the ships
 *  in the margin ring keep getting deltas instead of going stale until the next pan. */
const bboxOf = (map: maplibregl.Map): string =>
  pad(boundsOf(map)).map((v) => v.toFixed(4)).join(',');

const covers = (box: Bbox, [w, s, e, n]: Bbox): boolean =>
  box[0] <= w && box[1] <= s && box[2] >= e && box[3] >= n;

const inside = ([w, s, e, n]: Bbox, [lon, lat]: [number, number]): boolean =>
  lon >= w && lon <= e && lat >= s && lat <= n;

async function fetchVessels(bbox: Bbox, zoom: number): Promise<Vessel[]> {
  const query = `bbox=${bbox.map((v) => v.toFixed(4)).join(',')}&zoom=${zoom.toFixed(2)}`;
  const response = await fetch(`/v1/map/snapshot?${query}`);
  if (!response.ok) throw new Error(`snapshot ${response.status}`);
  const { vessels } = (await response.json()) as { vessels: Vessel[] };
  return vessels;
}

/** F9 — the charted polygons, straight from /v1/map/ports. Static data: fetched
 *  once per page, then handed to every rebuilt source (a theme swap wipes them).
 *  A failed read is a map without polygons, not a map that fails. */
const PORTS = 'ports';
const PORT_FILL = 'ports-fill';
const PORT_LINE = 'ports-line';
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] };
let ports: Promise<FeatureCollection> | null = null;
const fetchPorts = (): Promise<FeatureCollection> =>
  (ports ??= fetch('/v1/map/ports')
    .then((response) => {
      if (!response.ok) throw new Error(`ports ${response.status}`);
      return response.json() as Promise<FeatureCollection>;
    })
    .catch((error: unknown) => {
      ports = null; // a style swap retries; a permanent failure just draws no polygons
      console.warn('map: no port polygons —', error);
      return EMPTY;
    }));

/** The frame's chart line: night's anchor ring, day's chart accent. */
const chartLine = (theme: 'night' | 'day') =>
  theme === 'night' ? tokens.color.night.vessel['anchor.ring'] : tokens.color.day['accent.chart'];

/** hover lifts fill .05 -> .14 and stroke 1.8 -> 2.4 (frame `anchorageLayer`).
 *  Only anchorages ever carry the state, so ports simply never take the lift. */
const onHover = (lifted: number, base: number): maplibregl.ExpressionSpecification => [
  'case',
  ['boolean', ['feature-state', 'hover'], false],
  lifted,
  base,
];

/** Under the fleet: a polygon is water, the ships sit on it. Called from
 *  `addVesselLayer` before the vessel layers exist, which is what puts it there. */
function addPortsLayer(map: maplibregl.Map, theme: 'night' | 'day') {
  if (map.getLayer(PORT_FILL)) return;
  const color = chartLine(theme);
  // generateId: the API rows carry no id, and setFeatureState needs one.
  map.addSource(PORTS, { type: 'geojson', data: EMPTY, generateId: true });
  map.addLayer({
    id: PORT_FILL,
    type: 'fill',
    source: PORTS,
    paint: { 'fill-color': color, 'fill-opacity': onHover(0.14, 0.05) },
  });
  map.addLayer({
    id: PORT_LINE,
    type: 'line',
    source: PORTS,
    paint: {
      'line-color': color,
      'line-width': onHover(2.4, 1.8),
      'line-dasharray': [10, 6],
    },
  });
  void fetchPorts().then((data) =>
    map.getSource<maplibregl.GeoJSONSource>(PORTS)?.setData(data),
  );
}

/** Sprites + source + layers. Re-run after every setStyle — a style swap wipes all
 *  of them, images included. Idempotent on live pieces: an existing source gets the
 *  data pushed (a theme swap can race the snapshot fetch), existing layers get their
 *  state colours repainted. */
function addVesselLayer(map: maplibregl.Map, theme: 'night' | 'day', data: FeatureCollection) {
  addPortsLayer(map, theme);
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
  const { filter, selection } = useUrlStore.getState();
  if (map.getLayer(HULL_LAYER)) {
    applySelection(map, selection, theme);
    map.setPaintProperty(SHADOW_LAYER, 'icon-color', tokens.color[theme].vessel.shadow);
    for (const id of PULSE_LAYERS) {
      map.setPaintProperty(id, 'circle-stroke-color', tokens.color[theme].vessel.silent);
    }
    applyFilter(map, filter, theme, selection);
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
    paint: { 'icon-color': iconColor(theme, selection) },
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
      'icon-image': selectedIcon(selection), // drawn at its own step: icon-size stays 1
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
      'icon-image': selectedIcon(selection),
    },
    paint: { 'icon-color': iconColor(theme, selection) },
  });
  applyFilter(map, filter, theme, selection);
}

/** F9 — the hover panel, at the cursor. The queue count is F19's own number, read
 *  from /v1/ports/{locode}: the polygons are cached for the life of the process
 *  and can never carry it. Until it lands — or if it never does — the tooltip
 *  keeps the honest placeholder rather than guessing a zero. */
interface Tip {
  x: number;
  y: number;
  name: string;
  locode: string;
  waiting: number | null;
}

/** ponytail: a Map keyed by locode, not a query cache. 60 s because the detector
 *  rewrites its snapshot every 30 s and F19 promises a number no older than five
 *  minutes — a stale count on a hover is worse than one extra GET. */
const QUEUE_TTL_MS = 60_000;
const queues = new Map<
  string,
  { waiting: Promise<number | null>; value: number | null; at: number }
>();

/** The promise is cached, not the value: mousemove fires ~60 times a second and
 *  every event during the first fetch's latency window must join it, not start
 *  its own GET. */
function queueFor(locode: string): Promise<number | null> {
  const held = queues.get(locode);
  if (held && Date.now() - held.at < QUEUE_TTL_MS) return held.waiting;
  const waiting = (async (): Promise<number | null> => {
    try {
      const response = await fetch(`/v1/ports/${locode}`);
      if (!response.ok) throw new Error(`port ${response.status}`);
      const body = (await response.json()) as { waiting_now: number | null };
      return body.waiting_now;
    } catch (error: unknown) {
      console.warn('port queue:', error);
      return null;
    }
  })();
  const entry = { waiting, value: held?.value ?? null, at: Date.now() };
  queues.set(locode, entry);
  void waiting.then((value) => {
    entry.value = value;
  });
  return waiting;
}

export function MapCanvas() {
  const container = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);

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

    /** F7 count line and F11 map key: ships inside the viewport — all of them,
     *  and the silent ones — from the fleet we already hold, because
     *  queryRenderedFeatures would only see what is currently painted.
     *
     *  One walk, two counters. This runs on every moveend AND every delta frame,
     *  so the O(fleet) tail is paid often enough that a second pass for the second
     *  number would be the whole cost again for nothing. */
    // F11 — until something has actually answered there is no fleet to count, and
    // "0 SHIPS" over a sea full of ships is precisely the lie /v1/regions returns a
    // null rather than tell. A failed snapshot must read as "we cannot tell".
    let answered = false;
    const recount = () => {
      const bounds = map.getBounds();
      let silent = 0;
      let ships = 0;
      for (const f of fleet.values()) {
        const [lon, lat] = f.geometry.coordinates as [number, number];
        if (!bounds.contains([lon, lat])) continue;
        ships += 1;
        if (f.properties.state === 'silent') silent += 1;
      }
      const live = useLiveStore.getState();
      live.setSilent(silent);
      live.setShips(answered ? ships : null);
      syncPulse();
    };

    // A snapshot sweeps its own box (see `loadSnapshot`), which is what retires a
    // ship and bounds this Map as a side effect. Ships outside every box ever
    // loaded still accumulate; ponytail: an LRU when that memory actually hurts.
    const upsert = (rows: Vessel[], fillOnly = false) => {
      answered = true; // rows from a read that worked: the count is knowable now
      for (const row of rows) {
        // A snapshot is a point-in-time read and can be older than a delta already
        // applied: it only fills ships we have never seen. Live deltas own updates.
        if (fillOnly && fleet.has(row[0])) continue;
        fleet.set(row[0], feature(row));
      }
      recount();
    };
    const push = () => {
      void map.getSource<maplibregl.GeoJSONSource>(SOURCE)?.setData(collection());
    };

    // The snapshot's own bbox (padded), and a generation so a slow response for an
    // old view can never clobber a newer one.
    let loaded: Bbox | null = null;
    let generation = 0;
    const loadSnapshot = (box: Bbox) => {
      const mine = ++generation;
      loaded = box;
      // What we hold as the request goes out. Anything that arrives on a delta
      // while it is in flight is NEWER than the answer, so it is not a candidate
      // for the sweep below and `fillOnly` still keeps its position.
      const held = [...fleet.keys()];
      return fetchVessels(box, map.getZoom())
        .then((rows) => {
          if (mine !== generation) return; // a newer view already asked
          // A snapshot is the whole truth for its box, so anything inside it that
          // the response did not carry has aged past the server's 24 h cut: she
          // stopped appearing in snapshots and stopped generating deltas, and left
          // alone she stays on the water forever at her last position, counted by
          // the number F11 puts on screen. Only inside the box that was loaded,
          // and only on success — a failed read knows nothing about anybody.
          const carried = new Set(rows.map((row) => row[0]));
          for (const mmsi of held) {
            if (carried.has(mmsi)) continue;
            const at = fleet.get(mmsi)?.geometry.coordinates as [number, number] | undefined;
            if (at && inside(box, at)) fleet.delete(mmsi);
          }
          upsert(rows, true);
          push();
        })
        .catch((error: unknown) => {
          if (mine === generation) loaded = null; // failed -> not loaded, try again on the next move
          console.warn('map: no vessel snapshot —', error);
        });
    };

    // The socket does not wait for the basemap: a slow tile host must not stall
    // the live feed, and the LIVE dot has to tell the truth from the first second.
    // setData is optional-chained — before `load` there is no source to push to,
    // and the layer is built from `collection()` when there is.
    const stopLive = startLive(
      () => bboxOf(map),
      (rows) => {
        upsert(rows);
        push();
      },
    );

    map.on('load', () => {
      void loadSnapshot(pad(boundsOf(map))).finally(() =>
        addVesselLayer(map, useUrlStore.getState().theme, collection()),
      );
    });

    map.on('moveend', () => {
      const c = map.getCenter();
      // A pan or jump out of the loaded box brings ships that no live delta would:
      // deltas only carry what moved, so a fresh view needs a fresh snapshot.
      const view = boundsOf(map);
      if (!loaded || !covers(loaded, view)) void loadSnapshot(pad(view));
      useUrlStore.getState().patch({ zoom: map.getZoom(), center: [c.lng, c.lat] });
      recount();
    });

    // F8/F10 — a tap selects, bare water clears. Both vessel layers are wired
    // because they are zoom-gated (rung 1 below z9, rung 2 above), and the plain
    // handler runs last: it publishes whichever ship the layer handlers found, or
    // undefined when the click landed on the sea.
    let hit: number | undefined;
    const pick = (event: maplibregl.MapLayerMouseEvent) => {
      hit = event.features?.[0]?.properties.mmsi as number | undefined;
    };
    map.on('click', LOZ_LAYER, pick);
    map.on('click', HULL_LAYER, pick);
    map.on('click', () => {
      useUrlStore.getState().patch({ selection: hit });
      hit = undefined;
    });

    // F9 — hover lifts the anchorage under the pointer and names it. Ports are
    // drawn but not interactive: only an anchorage has a queue to offer.
    let lifted: string | number | undefined;
    const drop = () => {
      if (lifted !== undefined) map.setFeatureState({ source: PORTS, id: lifted }, { hover: false });
      lifted = undefined;
      setTip(null);
    };
    map.on('mousemove', PORT_FILL, (event) => {
      const found = event.features?.find((f) => f.properties.kind === 'anchorage');
      if (!found) return drop();
      if (found.id !== lifted) {
        drop();
        lifted = found.id;
        map.setFeatureState({ source: PORTS, id: lifted }, { hover: true });
      }
      const locode = String(found.properties.locode);
      const name = String(found.properties.name);
      const held = queues.get(locode);
      setTip({ x: event.point.x, y: event.point.y, name, locode, waiting: held?.value ?? null });
      void queueFor(locode).then((waiting) =>
        setTip((shown) => (shown && shown.locode === locode ? { ...shown, waiting } : shown)),
      );
    });
    map.on('mouseleave', PORT_FILL, drop);

    // F6 — a region pick just flies: `moveend` fires at the end of the fly and is
    // the single fetch path (and what re-subscribes the socket), so fetching here
    // too only bought a second HGETALL whose answer was thrown away.
    mapView.goto = (bbox: Bbox) => {
      map.fitBounds(bbox, { duration: 600 });
    };

    let painted = useUrlStore.getState().theme;
    let filtered = useUrlStore.getState().filter;
    let chosen = useUrlStore.getState().selection;
    const unsubscribe = useUrlStore.subscribe((state) => {
      if (state.selection !== chosen) {
        chosen = state.selection;
        if (map.getLayer(HULL_LAYER)) {
          applySelection(map, chosen, state.theme);
          // opacity carries the selection too, so the ship whose card just opened
          // does not stay dimmed under an active filter.
          applyFilter(map, state.filter, state.theme, chosen);
        }
      }
      if (state.filter !== filtered) {
        filtered = state.filter;
        if (map.getLayer(HULL_LAYER)) applyFilter(map, state.filter, state.theme, state.selection);
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
      mapView.goto = null;
      stopLive();
      unsubscribe();
      if (raf) cancelAnimationFrame(raf);
      map.remove();
    };
  }, []);

  // h-full, not inset-0: maplibre's own CSS forces position:relative on the
  // container, which silently disables absolute positioning (height collapses to 0).
  // The tooltip is a sibling, not a child: maplibre owns the container's children.
  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      {tip && (
        <div
          role="tooltip"
          className="pointer-events-none absolute z-30 -translate-x-1/2 -translate-y-full"
          style={{ left: tip.x, top: tip.y - 10 }}
        >
          <div
            className="flex h-9 items-center whitespace-nowrap px-[14px] text-[13px]"
            style={{
              background: 'var(--chrome-tip-fill)',
              border: '1px solid var(--chrome-tip-border)',
              boxShadow: 'var(--chrome-tip-shadow)',
              color: 'var(--chrome-tip-ink)',
            }}
          >
            <span>
              {tip.name} anchorage —{' '}
              {tip.waiting === null ? (
                <span style={{ color: 'var(--chrome-tip-soft)' }}>queue counting starts soon</span>
              ) : (
                <span>{tip.waiting} waiting</span>
              )}
            </span>
          </div>
          <div
            style={{
              position: 'absolute',
              bottom: -9,
              left: 'calc(50% - 4.5px)',
              width: 9,
              height: 9,
              transform: 'rotate(45deg)',
              background: 'var(--chrome-tip-fill)',
              borderRight: '1px solid var(--chrome-tip-border)',
              borderBottom: '1px solid var(--chrome-tip-border)',
            }}
          />
        </div>
      )}
    </div>
  );
}
