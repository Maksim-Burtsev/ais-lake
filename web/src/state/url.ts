/** F10 — URL is the state. One store holds everything the address bar carries;
 *  a tiny sync layer reads location.search on boot and writes it back with
 *  history.replaceState on every change (no history spam while panning).
 *
 *  Adding a field: extend UrlState, add one row to PARAMS, done. Only `theme`
 *  and `region` are wired to UI today; zoom/center are declared so the map task
 *  has nothing to design.
 */

import { create } from 'zustand';
import { applyTheme, persistTheme, resolveTheme, type Theme } from './theme';

export type Center = [lon: number, lat: number];

export interface UrlState {
  theme: Theme;
  region: string;
  zoom?: number;
  center?: Center;
}

/** Launch region. The frames say "Black Sea" — illustrative; launch is the
 *  North Sea + English Channel (docs/design/FRAMES.md). */
export const DEFAULT_REGION = 'North Sea';

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
};

const KEYS = Object.keys(PARAMS) as (keyof UrlState)[];

export function readUrl(search: string, now: Date = new Date()): UrlState {
  const query = new URLSearchParams(search);
  const state: UrlState = { theme: resolveTheme(search, now), region: DEFAULT_REGION };
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
    ? { theme: 'night', region: DEFAULT_REGION }
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
