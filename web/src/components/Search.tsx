/** F5 search — docs/design/"ais-lake MVP Batch 1 Map Shell.dc.html" :: "Search open",
 *  empty state from "…Batch 4 Entry and States.dc.html" :: "Search with nothing to show".
 *
 *  GET /v1/search owns every word and every number here: the class, the flag and
 *  the status sentence are read off the wire, never composed locally, and the
 *  empty state quotes the two figures the server measured rather than a guess.
 *  The row silhouettes are the map's own `hullD()` curves as inline SVG, so a
 *  ship looks the same in the dropdown as she does in the water.
 *
 *  Accessibility: unlike the region picker (plain buttons in a labelled group,
 *  because a listbox with a divider and disabled rows would be a lie), this
 *  panel really is a listbox — every row is activatable, the highlight moves
 *  without focus moving, and ARIA allows the two `role="group"` headers inside
 *  it. So: combobox over listbox, `aria-activedescendant` carrying the
 *  highlight. The empty state has no options and is therefore not a listbox.
 *
 *  Deviations, deliberate:
 *   · ⏎ on a ship selects her (?sel=, T7) and flies the map there — F5's
 *     criterion says "enter opens story", but the story page is F12/M4 and does
 *     not exist. The frame's own caption is "⏎ flies the map to it".
 *   · ports never appear: /v1/search returns that group empty until M3 draws the
 *     port polygons. The day it fills, three things here have to move with it —
 *     `Results` needs a `ports` field, `rows` has to count it, and `activate`
 *     has to index it — or a query matching only ports renders "nothing".
 *   · "every sea" in the empty state is a colour, not a link — the full-page
 *     search across every sea is a later screen.
 *   · picking a sea repeats RegionPicker's two lines (patch the region, fly the
 *     bbox) rather than importing them; a third caller is when it becomes a helper.
 */

import { useEffect, useRef, useState } from 'react';
import limits from '../limits.json';
import { CMETA, hullD, iconOf } from '../map/hulls';
import { mapView, type Bbox } from '../map/view';
import { useLiveStore } from '../state/live';
import { useUrlStore } from '../state/url';

const PLACEHOLDER = 'Search a ship, a port, a sea…';
/** Long enough that a fast typist fires one request per word, short enough that
 *  the panel feels attached to the keyboard. */
const DEBOUNCE_MS = 120;
/** Half a degree either side: she fills the view without losing her neighbours. */
const HALO_DEG = 0.08;
const COUNT = new Intl.NumberFormat('en-US');

interface Ship {
  mmsi: number;
  name: string | null;
  flag: string | null;
  class: string | null;
  sym: string | null;
  state: string | null;
  sentence: string | null;
  sog: number | null;
  lat: number | null;
  lon: number | null;
  cog: number | null;
  age_h: number | null;
}
interface Sea {
  slug: string;
  name: string;
  bbox: Bbox;
  count: number | null;
}
interface Results {
  /** false = the lake never answered — no connection, or the query failed. `ships:
   *  []` then means "did not ask", never "nothing out there", and the empty state
   *  must not make a claim about a question that was never put. */
  answering: boolean;
  ships: Ship[];
  seas: Sea[];
  near: Ship | null;
  /** `region` is the display NAME of the box `live` was counted in — the server's
   *  own, since /v1/search takes no region and cannot know where the picker is. */
  searched: { live: number | null; seen_30d: number | null; region: string | null };
}

/** Both panels wear the region picker's chrome — the frames measured identical. */
const PANEL = {
  background: 'var(--chrome-picker-fill)',
  borderColor: 'var(--chrome-picker-border)',
  boxShadow: 'var(--chrome-picker-shadow)',
};
/** The frame anchors the panel under the (dimmed) chip strip, not under the
 *  field: left 139 = the field's own left edge, top 112 = 68 below it. */
const HANG = 'absolute top-[calc(100%+68px)] left-0 z-40 border';
const ink = (on: boolean) =>
  on ? 'var(--chrome-picker-ink-active)' : 'var(--chrome-picker-ink)';

const METRIC_INK: Record<string, string> = {
  underway: 'var(--chrome-search-underway)',
  anchored: 'var(--chrome-search-anchored)',
  silent: 'var(--chrome-search-silent)',
  moored: 'var(--chrome-search-moored)',
};

/** How old her last fix may be and still say where she IS (F27 — the same number
 *  the map and every region count enforce, read and never typed). */
const LIVE_WINDOW_H = limits.map_vessel_age_s.max / 3_600;

/** Her age when the fix is older than that, else null. The server lists ships with
 *  no recent fix on purpose — identity is real whether or not she is transmitting —
 *  so the row has to say which kind it is: without this a ship last heard forty days
 *  ago renders "Cargo · Netherlands · Moored", character-for-character the same as
 *  one heard thirty seconds ago, inside a panel whose own empty state says nothing
 *  called that is transmitting. */
const staleAge = (ship: Ship): number | null =>
  ship.age_h !== null && ship.age_h > LIVE_WINDOW_H ? ship.age_h : null;

const ago = (h: number) => (h >= 48 ? `${Math.round(h / 24)} d ago` : `${Math.round(h)} h ago`);

/** The refinery withholds the figure below this: `underway` is every nav_status
 *  outside (1, 5) — aground, not-under-command and 15 "undefined" included — so
 *  "Under way" at 0.0 kn is a state, not a speed, and printing the number states
 *  the contradiction twice in the teal under-way ink.
 *  ponytail: the same threshold now lives in two languages, which is the real debt.
 *  It belongs on the wire (or in the sentence, which already carries it) rather
 *  than in a constant each side keeps in step by hand. */
const UNDERWAY_MIN_SOG_KN = 0.5; // refinery/state.py::UNDERWAY_MIN_SOG_KN

/** The trailing figure. At anchor and moored carry none: how long she has been
 *  there needs an anchorage event with a start time (F19, M5), and the age of
 *  her last fix is a different fact that would read as that one. */
function metric(ship: Ship): string | null {
  const old = staleAge(ship);
  if (old !== null) return ago(old); // history, not a position: no present tense
  if (ship.state === 'underway' && ship.sog !== null && ship.sog >= UNDERWAY_MIN_SOG_KN) {
    return `${ship.sog.toFixed(1)} kn`;
  }
  if (ship.state === 'silent' && ship.age_h !== null) return `${Math.round(ship.age_h)} h`;
  return null;
}

function Hull({ ship }: { ship: Ship }) {
  const { cls } = iconOf(ship.sym ?? '', ship.state ?? '', ship.sog ?? 0);
  return (
    <svg
      width="22"
      height="22"
      viewBox="-11 -11 22 22"
      className="block flex-none"
      aria-hidden="true"
    >
      <path
        d={hullD(cls, 20, Math.max(2.6, 20 / CMETA[cls]!.lb))}
        transform={`rotate(${ship.cog ?? 0})`}
        fill={
          ship.state === 'underway'
            ? 'var(--chrome-search-hull)'
            : 'var(--chrome-search-hull-still)'
        }
      />
    </svg>
  );
}

function Header({ label }: { label: string }) {
  return (
    <div className="px-[16px] pt-[8px] pb-[6px] font-mono text-[10px] tracking-[0.18em] text-[var(--chrome-picker-section)]">
      {label}
    </div>
  );
}

/** One row of either kind. The highlight IS the whole keyboard affordance —
 *  a gold rail and a brightened title, no ring and no radius (frame 5b). */
function Row({
  id,
  on,
  onPick,
  onHover,
  children,
}: {
  id: string;
  on: boolean;
  onPick: () => void;
  onHover: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={on}
      onClick={onPick}
      onMouseMove={onHover}
      className="flex cursor-pointer items-center gap-[12px] border-0 border-l-2 px-[16px]"
      style={{
        borderLeftColor: on ? 'var(--chrome-search-focus)' : 'transparent',
        background: on ? 'var(--chrome-picker-active-fill)' : 'transparent',
      }}
    >
      {children}
    </div>
  );
}

export function Search() {
  const patch = useUrlStore((s) => s.patch);
  const setSearchOpen = useLiveStore((s) => s.setSearchOpen);
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Results | null>(null);
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const generation = useRef(0);

  const query = q.trim();

  useEffect(() => {
    // An emptied field hides the panel on its own, so the last answer is left
    // where it is: retyping shows it while the new read is in flight, which is
    // the region picker's no-flash rule.
    if (!query) return;
    // The generation is the staleness guard the region picker's comment asks
    // for: a slow answer to "gas" must never land on top of "gas khios".
    const mine = ++generation.current;
    const timer = window.setTimeout(() => {
      fetch(`/v1/search?q=${encodeURIComponent(query)}`)
        .then(async (r) => {
          if (!r.ok) throw new Error(`search ${r.status}`);
          const body = (await r.json()) as Results;
          if (!Array.isArray(body.ships) || !Array.isArray(body.seas)) {
            throw new Error('search: unexpected shape');
          }
          if (mine === generation.current) {
            setData(body);
            setCursor(0); // a new answer re-highlights its own first row
          }
        })
        .catch((error: unknown) => console.warn('search:', error));
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', away);
    return () => window.removeEventListener('pointerdown', away);
  }, [open]);

  const ships = data?.ships ?? [];
  const seas = data?.seas ?? [];
  const rows = ships.length + seas.length;
  const shown = open && query !== '' && data !== null;
  const empty = shown && rows === 0;

  useEffect(() => {
    setSearchOpen(shown);
    return () => setSearchOpen(false);
  }, [shown, setSearchOpen]);

  const goShip = (ship: Ship) => {
    setOpen(false);
    patch({ selection: ship.mmsi });
    if (ship.lat !== null && ship.lon !== null) {
      mapView.goto?.([
        ship.lon - HALO_DEG,
        ship.lat - HALO_DEG,
        ship.lon + HALO_DEG,
        ship.lat + HALO_DEG,
      ]);
    }
  };
  const goSea = (sea: Sea) => {
    setOpen(false);
    patch({ region: sea.name });
    mapView.goto?.(sea.bbox);
  };
  const activate = (index: number) => {
    const ship = ships[index];
    if (ship) return goShip(ship);
    const sea = seas[index - ships.length];
    if (sea) goSea(sea);
  };

  const key = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') {
      if (!open) return; // nothing of ours to close: the ship card may have it
      setOpen(false);
      input.current?.focus();
      // Consumed. React attaches this at the root, so without it the native event
      // still reaches the card's window listener and closing the dropdown also
      // closed the card and stripped ?sel= from the URL.
      event.preventDefault();
      return;
    }
    if (!shown || rows === 0) return;
    // Wrapping: at most nine rows and nothing scrolls, so the bottom is one
    // keypress from the top and there is no place in a list to lose.
    if (event.key === 'ArrowDown') setCursor((c) => (c + 1) % rows);
    else if (event.key === 'ArrowUp') setCursor((c) => (c + rows - 1) % rows);
    else if (event.key === 'Enter') activate(cursor);
    else return;
    event.preventDefault();
  };

  return (
    <div ref={box} className="relative flex h-[32px] max-w-[360px] flex-1 items-center">
      <div
        className="flex h-[32px] w-full items-center gap-[9px] border px-[11px]"
        style={{
          borderColor: open ? 'var(--chrome-search-focus)' : 'var(--chrome-hairline)',
          background: open ? 'var(--chrome-search-focus-fill)' : 'var(--chrome-search-fill)',
        }}
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 14 14"
          className="block flex-none"
          aria-hidden="true"
          stroke={open ? 'var(--chrome-search-focus)' : 'var(--chrome-search-ink)'}
          strokeWidth="1.3"
        >
          <circle cx="5.8" cy="5.8" r="4.4" fill="none" />
          <line x1="9.2" y1="9.2" x2="12.6" y2="12.6" />
        </svg>
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-label="Search"
          aria-expanded={shown && !empty}
          aria-controls="search-results"
          aria-activedescendant={shown && !empty ? `search-row-${cursor}` : undefined}
          autoComplete="off"
          placeholder={PLACEHOLDER}
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={key}
          className="w-full border-0 bg-transparent p-0 text-[13px] text-[var(--chrome-search-text)] caret-[var(--chrome-search-focus)] outline-none placeholder:text-[var(--chrome-search-placeholder)]"
        />
      </div>

      {shown && !empty && (
        <div
          id="search-results"
          role="listbox"
          aria-label="Search results"
          onMouseDown={(event) => event.preventDefault()} // keep the caret in the field
          className={`${HANG} w-[400px] pt-[8px] pb-[10px]`}
          style={PANEL}
        >
          {ships.length > 0 && <Header label="SHIPS" />}
          {ships.map((ship, i) => (
            <Row
              key={ship.mmsi}
              id={`search-row-${i}`}
              on={i === cursor}
              onPick={() => goShip(ship)}
              onHover={() => setCursor(i)}
            >
              <div className="py-[9px]">
                <Hull ship={ship} />
              </div>
              <div className="flex-1 py-[9px]">
                <div className="font-display text-[16px]" style={{ color: ink(i === cursor) }}>
                  {ship.name ?? `MMSI ${ship.mmsi}`}
                </div>
                <div className="mt-[1px] text-[11.5px] text-[var(--chrome-search-sub)]">
                  {[ship.class, ship.flag, ship.sentence].filter(Boolean).join(' · ')}
                </div>
              </div>
              {/* an age is not a state, so it is drawn in the still hull's ink
                  whatever state that last fix was in. */}
              <span
                className="font-mono text-[10px]"
                style={{
                  color:
                    staleAge(ship) !== null
                      ? 'var(--chrome-search-hull-still)'
                      : METRIC_INK[ship.state ?? ''],
                }}
              >
                {metric(ship)}
              </span>
            </Row>
          ))}

          {ships.length > 0 && seas.length > 0 && (
            <div className="my-[8px] h-px bg-[var(--chrome-picker-rule)]" />
          )}
          {seas.length > 0 && <Header label="SEAS" />}
          {seas.map((sea, i) => (
            <Row
              key={sea.slug}
              id={`search-row-${ships.length + i}`}
              on={ships.length + i === cursor}
              onPick={() => goSea(sea)}
              onHover={() => setCursor(ships.length + i)}
            >
              <span className="flex w-[20px] flex-none justify-center py-[8px]">
                <span className="h-[8px] w-[8px] border border-[var(--chrome-search-sea-mark)]" />
              </span>
              <div className="flex-1 py-[8px]">
                <div className="text-[14px]" style={{ color: ink(ships.length + i === cursor) }}>
                  {sea.name}
                </div>
                <div className="mt-[1px] text-[11.5px] text-[var(--chrome-search-sub)]">
                  {sea.count === null ? 'count unavailable' : `${COUNT.format(sea.count)} ships live`}
                </div>
              </div>
            </Row>
          ))}
        </div>
      )}

      {empty && <Empty q={query} data={data} />}
    </div>
  );
}

/** One TRY INSTEAD line: a mono key and the sentence it stands for. */
function Try({ k, last, children }: { k: string; last?: boolean; children: React.ReactNode }) {
  return (
    <div
      className="flex items-center gap-[12px] py-[11px]"
      style={{
        borderBottom: last ? undefined : '1px solid var(--chrome-search-empty-rule)',
      }}
    >
      <span className="w-[52px] flex-none font-mono text-[11px] text-[var(--chrome-picker-section)]">
        {k}
      </span>
      <span className="flex-1 text-[14px] text-[var(--chrome-picker-ink)]">{children}</span>
    </div>
  );
}

/** Frame 8c. Not a listbox: nothing here is pickable, it is an explanation.
 *
 *  The region is the server's `searched.region` — the box those `live` vessels were
 *  actually counted in — and not the picker's, which would quote a North Sea number
 *  as the Kattegat's. Null drops the clause: no substitute, since the whole point of
 *  the sentence is that it names what was measured. */
function Empty({ q, data }: { q: string; data: Results }) {
  const { live, seen_30d: seen, region } = data.searched;
  const near = data.near;
  const nearSub = near && [near.class, near.sentence].filter(Boolean).join(', ');
  const where = region ? ` in the ${region}` : '';
  // An empty list from a search that never ran is not a fact about this ship.
  if (!data.answering) {
    return (
      <div
        onMouseDown={(event) => event.preventDefault()}
        className={`${HANG} w-[440px] px-[24px] pt-[24px] pb-[22px]`}
        style={PANEL}
      >
        <div className="font-display text-[22px] font-medium text-[var(--chrome-picker-ink-active)]">
          The search is not answering
        </div>
        <p className="mt-[10px] text-[14px] leading-[1.6] text-[var(--chrome-search-sub)]">
          We could not ask, so this says nothing about where{' '}
          <span className="italic">{q}</span> is. The ships on the map are still
          moving — try again in a moment.
        </p>
      </div>
    );
  }
  return (
    <div
      onMouseDown={(event) => event.preventDefault()}
      className={`${HANG} w-[440px] px-[24px] pt-[24px] pb-[22px]`}
      style={PANEL}
    >
      <div className="font-display text-[22px] font-medium text-[var(--chrome-picker-ink-active)]">
        Nothing called <span className="italic">{q}</span> is transmitting
      </div>
      <p className="mt-[10px] text-[14px] leading-[1.6] text-[var(--chrome-search-sub)]">
        {live !== null && seen !== null
          ? `We searched ${COUNT.format(live)} vessels live${where} and ${COUNT.format(seen)} seen in the last thirty days. `
          : ''}
        A ship with her transponder off does not appear here at all.
      </p>
      <div className="h-px bg-[var(--chrome-hairline)]" style={{ margin: '20px 0 16px' }} />
      <div className="font-mono text-[10px] tracking-[0.16em] text-[var(--chrome-search-focus)]">
        TRY INSTEAD
      </div>
      <div className="mt-[10px] flex flex-col">
        <Try k="MMSI">search her nine-digit MMSI instead of the name</Try>
        <Try k="SEA" last={!near}>
          widen to <span className="text-[var(--chrome-search-focus)]">every sea</span>
          {region ? ` — she may be outside the ${region}` : ''}
        </Try>
        {near && (
          <Try k="NEAR" last>
            closest match:{' '}
            <span className="text-[var(--chrome-picker-ink-active)]">{near.name}</span>
            {nearSub && ` — ${nearSub}`}
          </Try>
        )}
      </div>
    </div>
  );
}
