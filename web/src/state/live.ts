/** F1 — the live socket. One connection for the whole app: it carries the
 *  viewport bbox and the cadence, and its health is what the LIVE dot shows.
 *
 *  Wire (api/app/live.py):
 *    server -> {"ts", "interval", "vessels": [[mmsi, lat, lon, cog, sog, state, sym], …]}
 *    client -> {"bbox": "minLon,minLat,maxLon,maxLat", "interval": 30}
 *  Empty frames arrive every tick on purpose — they are the heartbeat that
 *  separates "nothing moved" from "socket dead".
 *
 *  Socket health is NOT url state: it changes on its own and would spam the
 *  address bar. It lives in its own tiny store.
 */

import { create } from 'zustand';
import { useUrlStore } from './url';

export type Vessel = [
  mmsi: number,
  lat: number,
  lon: number,
  cog: number,
  sog: number,
  state: string,
  sym?: string, // the sprite token, appended later: older frames stop at `state`
];

interface Frame {
  ts: number;
  interval: number;
  vessels: Vessel[];
}

/** live = frames arriving · stale = connected but overdue · down = no socket. */
export type LiveStatus = 'live' | 'stale' | 'down';

interface LiveStore {
  status: LiveStatus;
  setStatus: (status: LiveStatus) => void;
  /** F7 — silent ships inside the current viewport. Counted by MapCanvas from the
   *  fleet it already holds; the count line reads it. */
  silent: number;
  setSilent: (silent: number) => void;
  /** F11 — every ship inside the current viewport, counted on the same fleet
   *  walk that produces `silent` (MapCanvas `recount`). The map key prints it.
   *  null = we cannot tell: nothing has answered yet, and a failed snapshot leaves
   *  an empty fleet whose honest reading is "unknown", not "0 ships". /v1/regions
   *  returns null for the same reason — an empty sea would be a lie. */
  ships: number | null;
  setShips: (ships: number | null) => void;
  /** F5 — the search panel is open, so the chips step back to .25 (the frame
   *  dims them). Ephemeral like the rest of this store: it must not reach the
   *  address bar, and one boolean does not earn a store of its own. */
  searchOpen: boolean;
  setSearchOpen: (searchOpen: boolean) => void;
}

export const useLiveStore = create<LiveStore>((set) => ({
  status: 'stale', // connecting: not proven alive, not known dead
  setStatus: (status) => set({ status }),
  silent: 0,
  setSilent: (silent) => set({ silent }),
  ships: null, // nothing has answered yet
  setShips: (ships) => set({ ships }),
  searchOpen: false,
  setSearchOpen: (searchOpen) => set({ searchOpen }),
}));

const RETRY_BASE_MS = 1_000;
const RETRY_CAP_MS = 30_000;
/** A tick this late means the feed is not keeping its own promise. */
const STALE_FACTOR = 2.5;

const backoff = (attempt: number): number => {
  const flat = Math.min(RETRY_BASE_MS * 2 ** attempt, RETRY_CAP_MS);
  return flat * (0.5 + Math.random() / 2); // jitter: don't reconnect the whole fleet at once
};

/** Open the live socket and keep it open. `bboxOf` is asked for the current
 *  viewport on every (re)connect and on every url change; `onFrame` gets the
 *  deltas. Returns a teardown. */
export function startLive(bboxOf: () => string | null, onFrame: (vessels: Vessel[]) => void) {
  const setStatus = useLiveStore.getState().setStatus;
  let socket: WebSocket | null = null;
  let attempt = 0;
  let retryTimer: number | undefined;
  let staleTimer: number | undefined;
  let sent = '';
  let stopped = false;

  const armStale = () => {
    window.clearTimeout(staleTimer);
    const wait = useUrlStore.getState().interval * 1000 * STALE_FACTOR;
    staleTimer = window.setTimeout(() => setStatus('stale'), wait);
  };

  const connect = () => {
    if (stopped) return;
    const query = new URLSearchParams({ interval: String(useUrlStore.getState().interval) });
    const bbox = bboxOf();
    if (bbox) query.set('bbox', bbox);
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${scheme}://${location.host}/v1/live?${query}`);

    socket.onopen = () => {
      setStatus('live');
      armStale();
    };
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as Frame | { error: string };
      if ('error' in message) {
        console.warn('live:', message.error);
        return;
      }
      attempt = 0; // a frame, not a mere open, is what proves the feed works
      setStatus('live');
      armStale();
      if (message.vessels.length) onFrame(message.vessels);
    };
    socket.onerror = () => socket?.close();
    socket.onclose = () => {
      socket = null;
      sent = '';
      setStatus('down');
      window.clearTimeout(staleTimer);
      if (!stopped) retryTimer = window.setTimeout(connect, backoff(attempt++));
    };
  };

  connect();

  // Pan, zoom or a new cadence: patch the open socket, no reconnect (F1/F6).
  const unsubscribe = useUrlStore.subscribe((state) => {
    if (socket?.readyState !== WebSocket.OPEN) return;
    const bbox = bboxOf();
    const patch = JSON.stringify({ ...(bbox ? { bbox } : {}), interval: state.interval });
    if (patch === sent) return;
    sent = patch;
    socket.send(patch);
    armStale();
  });

  return () => {
    stopped = true;
    unsubscribe();
    window.clearTimeout(retryTimer);
    window.clearTimeout(staleTimer);
    socket?.close();
  };
}
