/** F10 — URL is the state. One store holds everything the address bar carries;
 *  a tiny sync layer reads location.search on boot and writes it back with
 *  history.replaceState on every change (no history spam while panning).
 *
 *  Adding a field: extend UrlState, add one row to PARAMS, done. Only `theme`
 *  and `region` are wired to UI today; zoom/center are declared so the map task
 *  has nothing to design.
 */

import { create } from 'zustand';
import limits from '../limits.json';
import { applyTheme, persistTheme, resolveTheme, type Theme } from './theme';

export type Center = [lon: number, lat: number];

/** F7 filter chips. Absent = "All ships" — the default needs no name in the URL. */
export const VESSEL_FILTERS = ['tankers', 'cargo', 'anchored', 'silent'] as const;
export type VesselFilter = (typeof VESSEL_FILTERS)[number];

export interface UrlState {
  theme: Theme;
  region: string;
  /** Live cadence in seconds (F1). Server-clamped; the selector only offers REFRESH.options. */
  interval: number;
  zoom?: number;
  center?: Center;
  filter?: VesselFilter;
  /** F8/F10 — the tapped ship's MMSI. Absent = nothing selected, no card. */
  selection?: number;
  /** F13 — the opened silence: a gap event_id. Absent = the timeline. */
  gap?: string;
  /** F14 — the replay playhead, epoch seconds. Absent = the track's end, paused. */
  t?: number;
}

/** Launch region. The frames say "Black Sea" — illustrative; launch is the
 *  North Sea + English Channel (docs/design/FRAMES.md). */
export const DEFAULT_REGION = 'North Sea';

/** The limits table, copied verbatim from the repo-root limits.json (F27).
 *  Never hardcode a cadence anywhere else — the api enforces these same numbers. */
export const REFRESH = limits.map_refresh_s;

/** One row per URL-visible field: how it is named, parsed and printed.
 *  `serialize` returning null drops the param (default value = clean URL). */
interface ParamSpec<K extends keyof UrlState> {
  param: string;
  parse: (raw: string) => UrlState[K] | undefined;
  serialize: (value: UrlState[K]) => string | null;
}

type Params = { [K in keyof UrlState]-?: ParamSpec<K> };

const num = (raw: string): number | undefined => {
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
};

export const PARAMS: Params = {
  theme: {
    param: 'theme',
    parse: (raw) => (raw === 'night' || raw === 'day' ? raw : undefined),
    serialize: (value) => value,
  },
  region: {
    param: 'region',
    parse: (raw) => (raw.trim() === '' ? undefined : raw),
    serialize: (value) => (value === DEFAULT_REGION ? null : value),
  },
  interval: {
    param: 'r',
    parse: (raw) => (REFRESH.options.includes(Number(raw)) ? Number(raw) : undefined),
    serialize: (value) => (value === REFRESH.default ? null : String(value)),
  },
  zoom: {
    param: 'z',
    parse: num,
    serialize: (value) => (value === undefined ? null : value.toFixed(2)),
  },
  center: {
    param: 'c',
    parse: (raw) => {
      const [lon, lat] = raw.split(',').map(num);
      return lon === undefined || lat === undefined ? undefined : [lon, lat];
    },
    serialize: (value) => (value === undefined ? null : `${value[0].toFixed(4)},${value[1].toFixed(4)}`),
  },
  selection: {
    param: 'sel',
    // Nine digits or nothing: ?sel=banana is a typo in someone's address bar, not
    // a reason for the map to fail to boot.
    parse: (raw) => (/^\d{9}$/.test(raw) ? Number(raw) : undefined),
    serialize: (value) => (value === undefined ? null : String(value)),
  },
  gap: {
    param: 'gap',
    // An event_id, not free text: anything else in the address bar is a typo and
    // the page stays on the timeline rather than hunting for a gap that is not there.
    parse: (raw) => (/^[\w-]{1,64}$/.test(raw) ? raw : undefined),
    serialize: (value) => value ?? null,
  },
  t: {
    param: 't',
    // Whole epoch seconds. Out-of-range is not our problem here: the replay
    // clamps to the track it actually has.
    parse: (raw) => (/^\d{1,11}$/.test(raw) ? Number(raw) : undefined),
    serialize: (value) => (value === undefined ? null : String(Math.round(value))),
  },
  filter: {
    param: 'f',
    parse: (raw) =>
      (VESSEL_FILTERS as readonly string[]).includes(raw) ? (raw as VesselFilter) : undefined,
    serialize: (value) => value ?? null,
  },
};

const KEYS = Object.keys(PARAMS) as (keyof UrlState)[];

export function readUrl(search: string, now: Date = new Date()): UrlState {
  const query = new URLSearchParams(search);
  const state: UrlState = {
    theme: resolveTheme(search, now),
    region: DEFAULT_REGION,
    interval: REFRESH.default,
  };
  for (const key of KEYS) {
    if (key === 'theme') continue;
    const raw = query.get(PARAMS[key].param);
    if (raw === null) continue;
    const parsed = PARAMS[key].parse(raw);
    if (parsed !== undefined) Object.assign(state, { [key]: parsed });
  }
  return state;
}

export function writeUrl(state: UrlState, search: string): string {
  const query = new URLSearchParams(search);
  for (const key of KEYS) {
    const spec = PARAMS[key];
    const value = spec.serialize(state[key] as never);
    if (value === null) query.delete(spec.param);
    else query.set(spec.param, value);
  }
  const qs = query.toString();
  return qs === '' ? location.pathname : `${location.pathname}?${qs}`;
}

interface UrlStore extends UrlState {
  /** Patch any subset of the URL state; the sync layer republishes the URL. */
  patch: (next: Partial<UrlState>) => void;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

const initial: UrlState =
  typeof window === 'undefined'
    ? { theme: 'night', region: DEFAULT_REGION, interval: REFRESH.default }
    : readUrl(window.location.search);

export const useUrlStore = create<UrlStore>((set, get) => ({
  ...initial,
  patch: (next) => set(next),
  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set({ theme: get().theme === 'night' ? 'day' : 'night' }),
}));

/** Boot-time wiring: paint the resolved theme, then mirror every change into
 *  <html data-theme>, localStorage and the address bar. Returns an unsubscribe. */
export function startUrlSync(): () => void {
  applyTheme(useUrlStore.getState().theme);
  let previousTheme = useUrlStore.getState().theme;
  return useUrlStore.subscribe((state) => {
    if (state.theme !== previousTheme) {
      previousTheme = state.theme;
      applyTheme(state.theme);
      persistTheme(state.theme);
    }
    history.replaceState(history.state, '', writeUrl(state, window.location.search));
  });
}
